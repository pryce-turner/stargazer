# Systematic Test Fix Plan: Component-Based API Migration

## Overview

Update all tests to use the new component-based type API and local hydrate test strategy. The types have been refactored from a `files: list[IpFile]` pattern to individual component fields (`alignment`, `index`, `vcf`, `r1`, `r2`, etc.) with dedicated `update_*()` methods.

## Current State Analysis

### Type Definitions (NEW API - Source of Truth)

| Type | Component Fields | Update Methods |
|------|-----------------|----------------|
| `Reference` | `fasta`, `faidx`, `aligner_index` (list) | `update_fasta()`, `update_faidx()`, `update_aligner_index()` |
| `Alignment` | `alignment`, `index` | `update_alignment()`, `update_index()` |
| `Variants` | `vcf`, `index` | `update_vcf()`, `update_index()` |
| `Reads` | `r1`, `r2` | `update_r1()`, `update_r2()` |

### Hydrate Task (Routing)

The `hydrate()` task routes files to type fields via `TYPE_REGISTRY`:
- `("alignment", "alignment")` → `Alignment.alignment`
- `("alignment", "index")` → `Alignment.index`
- `("variants", "vcf")` → `Variants.vcf`
- `("variants", "index")` → `Variants.index`
- `("reads", "r1")` → `Reads.r1`
- `("reads", "r2")` → `Reads.r2`
- `("reference", "fasta")` → `Reference.fasta`
- `("reference", "faidx")` → `Reference.faidx`
- `("reference", "aligner_index")` → `Reference.aligner_index` (appends)

### Tests Using OLD API (Need Updating)

Tests currently use patterns that no longer exist in the type definitions:
- `files: list[IpFile]` field
- `bam_name: str` field
- `add_files()` method
- `get_bam_path()`, `get_bai_path()`, `get_r1_path()`, `get_r2_path()` methods

### Tests Using NEW API (Reference Examples)

- `tests/tasks/gatk/test_baserecalibrator.py` - Uses `upload_file()` + `hydrate()` pattern correctly
- `tests/helpers.py` - Provides helper functions using the new pattern

## Target State

1. All tests use component fields (`alignment`, `index`, `vcf`, etc.) instead of `files` list
2. Tests either:
   - Use `helpers.py` functions (`create_test_alignment()`, etc.) for integration tests
   - Use direct component assignment for simple unit tests
3. No references to deprecated methods (`add_files()`, `get_bam_path()`, etc.)
4. No references to deprecated fields (`files`, `bam_name`)

## Implementation Plan

### Phase 1: Types Tests (`tests/types/`)

These tests directly test the type classes and need the most significant updates.

#### 1.1 test_alignment.py (17 changes)

**Current broken patterns:**
- Uses `files=[bam_file, bai_file]` constructor argument
- Uses `bam_name="..."` constructor argument
- Uses `alignment.files` access
- Uses `alignment.get_bam_path()`, `alignment.get_bai_path()`
- Uses `alignment.add_files()`

**Fix strategy:**
```python
# OLD:
alignment = Alignment(
    sample_id="NA12829", bam_name="test.bam", files=[bam_file, bai_file]
)
bam_path = alignment.get_bam_path()

# NEW (using hydrate):
await default_client.upload_file(bam_path, keyvalues={...})
alignments = await hydrate({"type": "alignment", "sample_id": "NA12829"})
alignment = alignments[0]
# Access via alignment.alignment.local_path

# NEW (direct assignment for unit tests):
alignment = Alignment(sample_id="NA12829", alignment=bam_ipfile, index=bai_ipfile)
```

**Tests to update:**
- `test_alignment_fetch` - Use component fields
- `test_alignment_get_bam_path` - Replace with direct field access
- `test_alignment_get_bai_path` - Replace with direct field access
- `test_alignment_get_bai_path_none` - Test `alignment.index is None`
- `test_alignment_add_files` - Use `update_alignment()` + `update_index()`
- `test_alignment_add_files_empty_list` - Remove (no equivalent in new API)
- `test_alignment_add_files_missing_file` - Remove (handled by upload_file)
- `test_alignment_fetch_empty` - Keep, test raises when no components set
- `test_alignment_get_bam_path_not_found` - Replace with test for `alignment is None`
- `test_alignment_get_bam_path_not_cached` - Replace with test for `local_path is None`
- `test_alignment_metadata_properties` - Update to use component fields

#### 1.2 test_reads.py (15 changes)

**Current broken patterns:**
- Uses `files=[r1_file, r2_file]` constructor argument
- Uses `reads.files` access
- Uses `reads.get_r1_path()`, `reads.get_r2_path()`
- Uses `reads.add_files()`

**Fix strategy:**
```python
# OLD:
reads = Reads(sample_id="NA12829", files=[r1_file, r2_file])
r1_path = reads.get_r1_path()

# NEW:
reads = Reads(sample_id="NA12829", r1=r1_ipfile, r2=r2_ipfile)
r1_path = reads.r1.local_path  # After fetch
```

**Tests to update:**
- `test_reads_fetch` - Use `r1`, `r2` component fields
- `test_reads_get_paths` - Replace with direct field access
- `test_reads_get_r2_path_single_end` - Test `reads.r2 is None`
- `test_reads_add_files` - Use `update_r1()` + `update_r2()`
- `test_reads_add_files_empty_list` - Remove
- `test_reads_add_files_missing_file` - Remove
- `test_reads_fetch_empty` - Keep
- `test_reads_get_r1_path_not_found` - Replace with test for `r1 is None`
- `test_reads_get_r1_path_not_cached` - Replace with test for `local_path is None`
- `test_reads_with_read_group` - Keep (read_group still exists)

#### 1.3 test_variants.py (Similar to alignment)

**Fix strategy:**
- Replace `files` with `vcf`, `index` component fields
- Replace `get_vcf_path()` with `variants.vcf.local_path`
- Replace `add_files()` with `update_vcf()` + `update_index()`

#### 1.4 test_reference.py (Similar pattern)

**Fix strategy:**
- Replace `files` with `fasta`, `faidx`, `aligner_index` component fields
- Replace `get_fasta_path()` with `ref.fasta.local_path`

---

### Phase 2: Task Tests (`tests/tasks/`)

#### 2.1 GATK Tasks (Already Mostly Fixed)

These tests already use the hydrate pattern. Verify they work:

| File | Status | Notes |
|------|--------|-------|
| `test_baserecalibrator.py` | ✅ Good | Uses hydrate pattern |
| `test_genotypegvcf.py` | 🔍 Check | Verify helper functions |
| `test_combinegvcfs.py` | 🔍 Check | Verify helper functions |
| `test_mergebamalignment.py` | 🔍 Check | Verify helper functions |
| `test_sortsam.py` | 🔍 Check | Verify helper functions |
| `test_applybqsr.py` | 🔍 Check | Verify helper functions |
| `test_markduplicates.py` | 🔍 Check | Verify helper functions |

**For each, verify:**
1. Helper functions use `default_client.upload_file()` with correct keyvalues
2. Tests use `hydrate()` to get populated types
3. No references to `files` list, `add_files()`, or deprecated getters

#### 2.2 General Tasks

| File | Status | Notes |
|------|--------|-------|
| `test_bwa.py` | ⚠️ Needs Update | Uses IpFile constructor directly |
| `test_samtools.py` | ⚠️ Needs Update | Uses IpFile constructor directly |

**Fix for test_bwa.py:**
```python
# OLD:
fasta_file = IpFile(id="test-id", cid=test_cid, name="...", ...)
ref = Reference(build="GRCh38", fasta=fasta_file)

# NEW (acceptable for unit tests - direct IpFile assignment):
# This works because the type fields accept IpFile directly
# Just ensure keyvalues have correct type/component
fasta_file = IpFile(
    id="test-id",
    cid=test_cid,
    name="GRCh38_TP53.fa",
    size=...,
    keyvalues={"type": "reference", "component": "fasta", "build": "GRCh38"},
    created_at=datetime.now(),
)
ref = Reference(build="GRCh38", fasta=fasta_file)
```

---

### Phase 3: Workflow Tests (`tests/workflows/`)

#### 3.1 test_germline_workflow.py

This file needs significant updates to match the workflow implementation.

**Current pattern (likely broken):**
- May use old `files` list pattern
- May use deprecated methods

**Target pattern:**
- Use `helpers.py` functions to create fixtures
- Use `hydrate()` to get populated types for testing
- Match the workflow's actual usage patterns

---

### Phase 4: Helpers Update (`tests/helpers.py`)

The helpers file is already correct but may need enhancements:

1. **Add `create_test_bqsr_report()`** - For baserecalibrator tests
2. **Add `create_test_known_sites()`** - For BQSR tests with known variant sites
3. **Verify `component` keyvalues** - Ensure all helpers set correct component values

---

## Execution Order

### Week 1: Foundation
1. **Phase 1.1**: Fix `test_alignment.py` first (most complex, sets pattern)
2. **Phase 1.2**: Fix `test_reads.py` (similar to alignment)
3. **Phase 1.3**: Fix `test_variants.py`
4. **Phase 1.4**: Fix `test_reference.py`
5. Run: `pytest tests/types/ -v` to verify

### Week 2: Tasks
1. **Phase 2.1**: Audit GATK task tests, fix any issues
2. **Phase 2.2**: Fix `test_bwa.py` and `test_samtools.py`
3. Run: `pytest tests/tasks/ -v` to verify

### Week 3: Workflows & Polish
1. **Phase 3.1**: Fix `test_germline_workflow.py`
2. **Phase 4**: Enhance `helpers.py` if needed
3. Run: `pytest tests/ -v` for full suite

---

## Change Patterns Reference

### Pattern A: Direct Component Assignment (Unit Tests)

When you need to test type behavior without the full upload/hydrate flow:

```python
# Create IpFile with correct metadata
bam_ipfile = IpFile(
    id="test-id",
    cid="QmTest123",
    name="sample.bam",
    size=1000,
    keyvalues={
        "type": "alignment",
        "component": "alignment",  # REQUIRED
        "sample_id": "NA12829",
    },
    created_at=datetime.now(),
    local_path=cached_bam,  # Set if file exists in cache
)

# Assign to type
alignment = Alignment(sample_id="NA12829", alignment=bam_ipfile)
```

### Pattern B: Upload + Hydrate (Integration Tests)

When you need to test the full data flow:

```python
# Create file and upload
bam_path = local_dir / "sample.bam"
bam_path.write_bytes(b"BAM\x01content")

await default_client.upload_file(
    bam_path,
    keyvalues={
        "type": "alignment",
        "component": "alignment",
        "sample_id": "NA12829",
    },
)

# Hydrate to get populated type
alignments = await hydrate({"type": "alignment", "sample_id": "NA12829"})
alignment = alignments[0]
```

### Pattern C: Using helpers.py (Recommended for Integration Tests)

```python
from helpers import create_test_alignment, create_test_reference


@pytest.mark.asyncio
async def test_some_task():
    alignment = await create_test_alignment("NA12829")
    ref = await create_test_reference("GRCh38")

    result = await some_task(alignment=alignment, ref=ref)
    ...
```

---

## Files to Update Summary

### Must Update (Use OLD API)
- `tests/types/test_alignment.py` - ~15 tests
- `tests/types/test_reads.py` - ~12 tests
- `tests/types/test_variants.py` - ~10 tests (estimate)
- `tests/types/test_reference.py` - ~8 tests (estimate)
- `tests/tasks/general/test_bwa.py` - 3 tests
- `tests/tasks/general/test_samtools.py` - ~5 tests (estimate)
- `tests/workflows/test_germline_workflow.py` - ~5 tests (estimate)

### Verify Only (May be OK)
- `tests/tasks/gatk/test_baserecalibrator.py` - Uses new pattern
- `tests/tasks/gatk/test_*.py` - Check each

### No Changes Expected
- `tests/utils/test_pinata.py`
- `tests/utils/test_query.py`
- `tests/utils/test_subprocess.py`
- `tests/conftest.py`
- `tests/config.py`

---

## Validation Checkpoints

After each phase, run these checks:

```bash
# Type tests
pytest tests/types/ -v --tb=short

# Task tests
pytest tests/tasks/ -v --tb=short

# Workflow tests
pytest tests/workflows/ -v --tb=short

# Full suite
pytest tests/ -v

# With coverage
pytest tests/ -v --cov=stargazer --cov-report=term-missing
```

---

## Risks & Mitigations

1. **Breaking existing passing tests**: Run tests before/after each file change
2. **Inconsistent patterns across tests**: Use helpers.py consistently
3. **Missing keyvalue metadata**: Always include `type` and `component` in keyvalues
4. **Test isolation failures**: Rely on `conftest.py` fixture for isolation (already good)
