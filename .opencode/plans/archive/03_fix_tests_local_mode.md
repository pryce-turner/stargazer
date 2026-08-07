# Fix Tests for Local Mode Plan

## Overview

Update all tests to use the new local mode hydration function and renamed `local_dir` property (formerly `cache_dir`). This aligns the test suite with the local filesystem implementation described in `local_filesystem.md`.

## Current State

- Tests reference `default_client.cache_dir` which no longer exists
- Tests create mock files directly in the cache directory
- Tests don't use the new TinyDB-backed metadata storage
- Source code still has `pinata_hydrate` references that should use the new `hydrate` task
- Tests fail because `PinataClient` no longer has a `cache_dir` attribute (renamed to `local_dir`)

## Target State

- All tests use `default_client.local_dir` instead of `default_client.cache_dir`
- Tests that mock local files properly register metadata in TinyDB
- Tests work in local-only mode without network requirements
- Source code uses the general `hydrate` task from `stargazer.tasks.hydrate`

## Implementation Plan

### Phase 1: Simple Rename - `cache_dir` to `local_dir`

Update all test files that reference `cache_dir` to use `local_dir` instead.

**Files to update:**

| File | Lines to Change |
|------|-----------------|
| `tests/test_samtools.py` | 29, 30, 95, 99, 100 |
| `tests/test_reads.py` | 27, 28, 29, 60, 63, 64, 86, 87, 88, 143, 144 |
| `tests/test_variants.py` | 33, 34, 35, 71, 74, 75, 105, 106, 152, 153, 154, 212, 213 |
| `tests/test_reference.py` | 108, 113, 114 |
| `tests/test_alignment.py` | 27, 28, 29, 66, 69, 70, 92, 93, 133, 134, 135, 186, 187 |
| `tests/test_bwa.py` | 29, 30, 107, 111, 132, 164 |
| `tests/test_germline_workflow.py` | 33, 34, 75, 76, 77, 124, 125 |
| `tests/test_baserecalibrator.py` | 25, 26, 56, 57, 92, 94 |
| `tests/test_genotypegvcf.py` | 25, 26, 70, 71, 108, 112 |
| `tests/test_combinegvcfs.py` | 25, 26, 70, 71, 106, 119, 237, 241 |
| `tests/test_mergebamalignment.py` | 28, 29, 58, 59, 95, 98, 100 |
| `tests/test_sortsam.py` | 25, 26, 54, 55, 90, 92 |
| `tests/test_applybqsr.py` | 25, 26, 56, 57, 88, 89, 131, 133, 135 |
| `tests/test_markduplicates.py` | 25, 26, 55, 56, 91, 93 |

**Change pattern:**
```python
# Before
default_client.cache_dir.mkdir(parents=True, exist_ok=True)
cached_file = default_client.cache_dir / test_cid

# After
default_client.local_dir.mkdir(parents=True, exist_ok=True)
cached_file = default_client.local_dir / test_cid
```

### Phase 2: Update Helper Functions with `cache_dir` Parameter

Some test files have helper functions that accept `cache_dir` as a parameter. Update these function signatures and calls.

**Files with helper functions:**

| File | Helper Function | Lines |
|------|-----------------|-------|
| `tests/test_baserecalibrator.py` | `create_mock_alignment()`, `create_mock_reference()` | 17, 49 |
| `tests/test_genotypegvcf.py` | `create_mock_variants()`, `create_mock_reference()` | 17, 63 |
| `tests/test_combinegvcfs.py` | `create_mock_variants()`, `create_mock_reference()` | 17, 63 |
| `tests/test_mergebamalignment.py` | `create_mock_alignment()`, `create_mock_reference()` | 17, 51 |
| `tests/test_sortsam.py` | `create_mock_alignment()`, `create_mock_reference()` | 17, 47 |
| `tests/test_applybqsr.py` | `create_mock_alignment()`, `create_mock_reference()`, `create_mock_recal_report()` | 17, 49, 80 |
| `tests/test_markduplicates.py` | `create_mock_alignment()`, `create_mock_reference()` | 17, 48 |

**Change pattern:**
```python
# Before
def create_mock_alignment(
    cache_dir: Path, sample_id: str, test_cid: str
) -> tuple[Path, Alignment, IpFile]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    bam_path = cache_dir / test_cid


# After
def create_mock_alignment(
    local_dir: Path, sample_id: str, test_cid: str
) -> tuple[Path, Alignment, IpFile]:
    local_dir.mkdir(parents=True, exist_ok=True)
    bam_path = local_dir / test_cid
```

### Phase 3: Update Source Code `pinata_hydrate` to `hydrate`

Replace deprecated `pinata_hydrate` class method calls with the general `hydrate` task.

**Files to update:**

| File | Lines | Current Usage |
|------|-------|---------------|
| `src/stargazer/workflows/gatk_data_preprocessing.py` | 56, 121 | `Reference.pinata_hydrate()`, `Reads.pinata_hydrate()` |
| `src/stargazer/workflows/germline_short_variant_discovery.py` | 63, 101 | `Reference.pinata_hydrate()`, `Reads.pinata_hydrate()` |
| `src/stargazer/tasks/bwa.py` | 122 | `Reads.pinata_hydrate()` (docstring example) |

**Change pattern:**
```python
# Before
from stargazer.assets import Reference, Reads

ref = await Reference.pinata_hydrate(ref_name=ref_name)
reads = await Reads.pinata_hydrate(sample_id=sample_id)

# After
from stargazer.tasks import hydrate

refs = await hydrate({"type": "reference", "build": ref_name})
ref = refs[0] if refs else None

reads_list = await hydrate({"type": "reads", "sample_id": sample_id})
reads = reads_list[0] if reads_list else None
```

### Phase 4: Update Agent Documentation

Update agent markdown files that contain stale `cache_dir` or `pinata_hydrate` references.

**Files to update:**

| File | Reference | Action |
|------|-----------|--------|
| `.opencode/plans/keyvalue_components.md` | `cache_dir` references | Update to `local_dir` |
| `.opencode/agent/code-review.md` | `pinata_hydrate` | Update to `hydrate` |
| `.opencode/agent/workflow.md` | `pinata_hydrate` | Update to `hydrate` |

### Phase 5: Refactor Tests to Use `hydrate` Function

Replace manual `IpFile` and type construction in tests with the proper `upload_file()` + `hydrate()` pattern. This makes tests more realistic and exercises the actual hydration code path.

**Current Pattern (Manual Construction):**
```python
def create_mock_alignment(
    local_dir: Path, sample_id: str, test_cid: str
) -> tuple[Path, IpFile]:
    local_dir.mkdir(parents=True, exist_ok=True)
    bam_path = local_dir / test_cid
    bam_path.write_bytes(b"BAM\x01mock_bam_content")

    # Manual IpFile construction - doesn't use TinyDB
    ipfile = IpFile(
        id=f"test-{sample_id}-bam",
        cid=test_cid,
        name=f"{sample_id}.bam",
        size=bam_path.stat().st_size,
        keyvalues={
            "type": "alignment",
            "sample_id": sample_id,
            "component": "alignment",
        },
        created_at=datetime.now(),
    )
    return bam_path, ipfile


# Then manual type construction
alignment = Alignment(
    sample_id=sample_id, bam_name=f"{sample_id}.bam", files=[bam_ipfile]
)
```

**New Pattern (Using `upload_file()` + `hydrate()`):**
```python
from stargazer.tasks import hydrate
from stargazer.utils.pinata import default_client


async def create_mock_alignment(sample_id: str) -> tuple[Path, Alignment]:
    """Create mock alignment file and return hydrated type."""
    # Create temp file with mock content
    local_dir = default_client.local_dir
    local_dir.mkdir(parents=True, exist_ok=True)
    bam_path = local_dir / f"{sample_id}.bam"
    bam_path.write_bytes(b"BAM\x01mock_bam_content")

    # Upload registers in TinyDB (in local_only mode)
    await default_client.upload_file(
        bam_path,
        keyvalues={
            "type": "alignment",
            "component": "alignment",
            "sample_id": sample_id,
        },
    )

    # Hydrate returns properly constructed type
    alignments = await hydrate({"type": "alignment", "sample_id": sample_id})
    return bam_path, alignments[0]
```

**Prerequisites:**

1. **Configure `conftest.py` for local-only mode:**
```python
# tests/conftest.py
import os
import pytest
from pathlib import Path
from stargazer.utils.pinata import default_client


@pytest.fixture(autouse=True)
def setup_local_only_mode(tmp_path):
    """Configure tests to run in local-only mode with isolated TinyDB."""
    # Set local-only mode
    original_local_only = default_client.local_only
    original_local_dir = default_client.local_dir
    original_db = default_client._db

    # Use temp directory for test isolation
    test_local_dir = tmp_path / "stargazer_test"
    test_local_dir.mkdir(parents=True, exist_ok=True)

    default_client.local_only = True
    default_client.local_dir = test_local_dir
    default_client.local_db_path = test_local_dir / "stargazer_local.json"
    default_client._db = None  # Reset to trigger lazy init with new path

    yield

    # Restore original settings
    default_client.local_only = original_local_only
    default_client.local_dir = original_local_dir
    default_client._db = original_db
```

**Files to refactor:**

| File | Helper Functions to Convert |
|------|----------------------------|
| `tests/test_baserecalibrator.py` | `create_mock_bam()`, `create_mock_reference()` |
| `tests/test_genotypegvcf.py` | `create_mock_variants()`, `create_mock_reference()` |
| `tests/test_combinegvcfs.py` | `create_mock_variants()`, `create_mock_reference()` |
| `tests/test_mergebamalignment.py` | `create_mock_alignment()`, `create_mock_reference()` |
| `tests/test_sortsam.py` | `create_mock_alignment()`, `create_mock_reference()` |
| `tests/test_applybqsr.py` | `create_mock_alignment()`, `create_mock_reference()`, `create_mock_recal_report()` |
| `tests/test_markduplicates.py` | `create_mock_alignment()`, `create_mock_reference()` |
| `tests/test_alignment.py` | Direct `IpFile` construction |
| `tests/test_reads.py` | Direct `IpFile` construction |
| `tests/test_variants.py` | Direct `IpFile` construction |
| `tests/test_reference.py` | Direct `IpFile` construction |
| `tests/test_bwa.py` | Direct `IpFile` construction |
| `tests/test_samtools.py` | Direct `IpFile` construction |
| `tests/test_germline_workflow.py` | Direct `IpFile` construction |

**Shared Test Utilities:**

Create a shared module `tests/helpers.py` with reusable async helpers:

```python
# tests/helpers.py
"""Shared test helpers for creating mock data via hydration."""

from pathlib import Path
from stargazer.tasks import hydrate
from stargazer.assets import Reference, Alignment, Variants, Reads
from stargazer.utils.pinata import default_client


async def create_test_reference(build: str = "GRCh38") -> Reference:
    """Create and hydrate a mock reference."""
    local_dir = default_client.local_dir
    local_dir.mkdir(parents=True, exist_ok=True)

    # Create FASTA
    fasta_path = local_dir / f"{build}.fa"
    fasta_path.write_text(f">chr1\nGATCGATCGATC\n")
    await default_client.upload_file(
        fasta_path,
        keyvalues={"type": "reference", "component": "fasta", "build": build},
    )

    # Create FAIDX
    faidx_path = local_dir / f"{build}.fa.fai"
    faidx_path.write_text("chr1\t12\t6\t12\t13\n")
    await default_client.upload_file(
        faidx_path,
        keyvalues={"type": "reference", "component": "faidx", "build": build},
    )

    refs = await hydrate({"type": "reference", "build": build})
    return refs[0]


async def create_test_alignment(sample_id: str) -> Alignment:
    """Create and hydrate a mock alignment."""
    local_dir = default_client.local_dir
    local_dir.mkdir(parents=True, exist_ok=True)

    # Create BAM
    bam_path = local_dir / f"{sample_id}.bam"
    bam_path.write_bytes(b"BAM\x01mock_content")
    await default_client.upload_file(
        bam_path,
        keyvalues={
            "type": "alignment",
            "component": "alignment",
            "sample_id": sample_id,
        },
    )

    # Create BAI
    bai_path = local_dir / f"{sample_id}.bam.bai"
    bai_path.write_bytes(b"BAI\x01mock_index")
    await default_client.upload_file(
        bai_path,
        keyvalues={"type": "alignment", "component": "index", "sample_id": sample_id},
    )

    alignments = await hydrate({"type": "alignment", "sample_id": sample_id})
    return alignments[0]


async def create_test_reads(sample_id: str, paired: bool = True) -> Reads:
    """Create and hydrate mock reads."""
    local_dir = default_client.local_dir
    local_dir.mkdir(parents=True, exist_ok=True)

    # Create R1
    r1_path = local_dir / f"{sample_id}_R1.fastq"
    r1_path.write_text("@read1\nACGT\n+\nIIII\n")
    await default_client.upload_file(
        r1_path, keyvalues={"type": "reads", "component": "r1", "sample_id": sample_id}
    )

    if paired:
        # Create R2
        r2_path = local_dir / f"{sample_id}_R2.fastq"
        r2_path.write_text("@read1\nTGCA\n+\nIIII\n")
        await default_client.upload_file(
            r2_path,
            keyvalues={"type": "reads", "component": "r2", "sample_id": sample_id},
        )

    reads_list = await hydrate({"type": "reads", "sample_id": sample_id})
    return reads_list[0]


async def create_test_variants(sample_id: str) -> Variants:
    """Create and hydrate mock variants."""
    local_dir = default_client.local_dir
    local_dir.mkdir(parents=True, exist_ok=True)

    # Create VCF
    vcf_path = local_dir / f"{sample_id}.vcf.gz"
    vcf_path.write_bytes(b"\x1f\x8b")  # gzip magic bytes
    await default_client.upload_file(
        vcf_path,
        keyvalues={"type": "variants", "component": "vcf", "sample_id": sample_id},
    )

    # Create TBI
    tbi_path = local_dir / f"{sample_id}.vcf.gz.tbi"
    tbi_path.write_bytes(b"TBI\x01")
    await default_client.upload_file(
        tbi_path,
        keyvalues={"type": "variants", "component": "index", "sample_id": sample_id},
    )

    variants_list = await hydrate({"type": "variants", "sample_id": sample_id})
    return variants_list[0]
```

**Example Refactored Test:**

```python
# tests/test_baserecalibrator.py (refactored)

import pytest
from helpers import create_test_alignment, create_test_reference
from stargazer.tasks.gatk.baserecalibrator import baserecalibrator


@pytest.mark.asyncio
async def test_baserecalibrator_creates_report():
    """Test that baserecalibrator creates a recalibration report."""
    # Use hydration-based helpers
    alignment = await create_test_alignment("NA12878_bqsr")
    ref = await create_test_reference("GRCh38")

    recal_report = await baserecalibrator(
        alignment=alignment,
        ref=ref,
        known_sites=["dbsnp_146.hg38.vcf.gz"],
    )

    assert recal_report.keyvalues.get("type") == "bqsr_report"
    assert recal_report.keyvalues.get("sample_id") == "NA12878_bqsr"
```

**Benefits of This Approach:**

1. **Tests exercise real hydration code** - Catches bugs in the `hydrate()` function
2. **TinyDB metadata is properly populated** - Tests are more realistic
3. **Less manual boilerplate** - Shared helpers reduce duplication
4. **Automatic cleanup via `tmp_path`** - pytest handles temp directory cleanup
5. **Isolated test databases** - Each test gets its own TinyDB instance
6. **Aligns with production patterns** - Tests mirror how production code uses hydration

### Phase 7: Final Verification

1. Run full test suite: `pytest tests/ -v`
2. Verify local-only mode works: `STARGAZER_LOCAL_ONLY=1 pytest tests/ -v`
3. Check test isolation: ensure no test pollution between runs
4. Verify TinyDB cleanup after tests

## File Changes Summary

### Phases 1-4: Rename and Source Code Updates

| File | Changes |
|------|---------|
| `tests/test_samtools.py` | `cache_dir` → `local_dir` |
| `tests/test_reads.py` | `cache_dir` → `local_dir` |
| `tests/test_variants.py` | `cache_dir` → `local_dir` |
| `tests/test_reference.py` | `cache_dir` → `local_dir` |
| `tests/test_alignment.py` | `cache_dir` → `local_dir` |
| `tests/test_bwa.py` | `cache_dir` → `local_dir` |
| `tests/test_germline_workflow.py` | `cache_dir` → `local_dir` |
| `tests/test_baserecalibrator.py` | `cache_dir` → `local_dir` + helper functions |
| `tests/test_genotypegvcf.py` | `cache_dir` → `local_dir` + helper functions |
| `tests/test_combinegvcfs.py` | `cache_dir` → `local_dir` + helper functions |
| `tests/test_mergebamalignment.py` | `cache_dir` → `local_dir` + helper functions |
| `tests/test_sortsam.py` | `cache_dir` → `local_dir` + helper functions |
| `tests/test_applybqsr.py` | `cache_dir` → `local_dir` + helper functions |
| `tests/test_markduplicates.py` | `cache_dir` → `local_dir` + helper functions |
| `src/stargazer/workflows/gatk_data_preprocessing.py` | `pinata_hydrate` → `hydrate` |
| `src/stargazer/workflows/germline_short_variant_discovery.py` | `pinata_hydrate` → `hydrate` |
| `src/stargazer/tasks/bwa.py` | Update docstring example |
| `.opencode/plans/keyvalue_components.md` | `cache_dir` → `local_dir` |
| `.opencode/agent/code-review.md` | `pinata_hydrate` → `hydrate` |
| `.opencode/agent/workflow.md` | `pinata_hydrate` → `hydrate` |

### Phase 6: Hydrate Refactor (New/Modified Files)

| File | Changes |
|------|---------|
| `tests/helpers.py` | **NEW** - Shared async helpers for creating test data via hydration |
| `tests/conftest.py` | Add `setup_local_only_mode` fixture for test isolation |
| `tests/test_baserecalibrator.py` | Replace manual `IpFile` construction with `helpers.create_test_*()` |
| `tests/test_genotypegvcf.py` | Replace manual `IpFile` construction with `helpers.create_test_*()` |
| `tests/test_combinegvcfs.py` | Replace manual `IpFile` construction with `helpers.create_test_*()` |
| `tests/test_mergebamalignment.py` | Replace manual `IpFile` construction with `helpers.create_test_*()` |
| `tests/test_sortsam.py` | Replace manual `IpFile` construction with `helpers.create_test_*()` |
| `tests/test_applybqsr.py` | Replace manual `IpFile` construction with `helpers.create_test_*()` |
| `tests/test_markduplicates.py` | Replace manual `IpFile` construction with `helpers.create_test_*()` |
| `tests/test_alignment.py` | Use `hydrate()` instead of manual construction |
| `tests/test_reads.py` | Use `hydrate()` instead of manual construction |
| `tests/test_variants.py` | Use `hydrate()` instead of manual construction |
| `tests/test_reference.py` | Use `hydrate()` instead of manual construction |
| `tests/test_bwa.py` | Use `hydrate()` instead of manual construction |
| `tests/test_samtools.py` | Use `hydrate()` instead of manual construction |
| `tests/test_germline_workflow.py` | Use `hydrate()` instead of manual construction |

## Design Decisions

1. **Simple find-replace for `cache_dir`**: The rename from `cache_dir` to `local_dir` is a straightforward refactor with no behavioral change. Apply globally.

2. **Keep helper function signatures consistent**: Rename the parameter from `cache_dir` to `local_dir` in all helper functions to maintain consistency.

3. **`hydrate` task over class methods**: The general `hydrate` task is the new pattern. It queries by keyvalues and returns typed instances. Class methods like `pinata_hydrate` should be deprecated.

4. **Local-only mode as default for tests**: Tests should work in local-only mode by default. Network-dependent tests should be skipped when `PINATA_JWT` is not set.

5. **Update documentation**: Keep agent docs in sync with implementation to avoid confusion during development.

6. **Use `upload_file()` + `hydrate()` for test fixtures**: Instead of manually constructing `IpFile` objects with hardcoded values, tests should use the actual upload and hydration code paths. This ensures:
   - TinyDB metadata is properly populated
   - Tests exercise real production code paths
   - Test fixtures match production data structures

7. **Shared test helpers**: Create a `tests/helpers.py` module with reusable async functions for creating common test data (reference, alignment, reads, variants). This reduces boilerplate and ensures consistency.

8. **Test isolation via `tmp_path`**: Use pytest's `tmp_path` fixture to give each test an isolated `local_dir` and TinyDB instance. This prevents test pollution and ensures clean state.

## Execution Order

1. **Phase 1** (simple rename) - Safe, mechanical changes - **Prerequisite for all other phases**
2. **Phase 2** (helper functions) - Still mechanical, slightly more involved
3. **Phase 3** (source code) - Requires understanding of `hydrate` task behavior
4. **Phase 4** (documentation) - Low priority, can be done last
5. **Phase 5** (intermediate verification) - Run tests to confirm Phases 1-4 work
6. **Phase 6** (hydrate refactor) - Major refactor of test fixtures to use `hydrate()`
7. **Phase 7** (final verification) - Full test suite validation

**Alternative Execution Path:**

If time is limited, Phases 1-5 can be completed first to get tests passing. Phase 6 (hydrate refactor) is an enhancement that improves test quality but isn't strictly required for functionality.
