# Filesystem DB Generation Tool

## Problem Statement

The existing `tests/fixtures/build_local_db.py` hardcodes a mapping of filenames to keyvalues metadata. This works for a known set of test fixtures but does not scale. The goal is a production-grade, interactive tool exposed as an MCP tool that scans a directory of genomics files, infers what it can from extensions and filenames, introspects file contents when needed, and prompts the LLM on ambiguity — ultimately writing a `stargazer_local.json` TinyDB database for `LocalStorageClient`.

## Design Decisions (Resolved)

- **Stage inference**: Do NOT infer pipeline stage or cumulative state from filenames. Only set required metadata (type, component, sample_id, build). Stage and derived flags are determined interactively or from file contents.
- **Caller inference**: Do NOT auto-infer caller. Specified interactively when needed.
- **Recursion**: Recursive scanning with relative paths in `rel_path` (e.g., `subdir/file.bam`). Requires `LocalStorageClient` to resolve `rel_path` relative to `local_dir`.
- **Overrides**: No sidecar file. The tool is interactive — exposed as an MCP tool where the LLM can prompt on ambiguity and the user can provide metadata interactively.
- **Introspection**: The tool has access to file introspection helpers (read BAM/CRAM headers via samtools, read VCF headers, etc.) to resolve ambiguous metadata from file contents.

## Keyvalues Schema

Every TinyDB record has top-level structure: `{id, cid, name, size, keyvalues, created_at, is_public, rel_path}`. The `keyvalues` dict contains:

### Core Fields (always present)

| Field | Values | Description |
|-------|--------|-------------|
| `type` | `reference`, `reads`, `alignment`, `variants`, `known_sites`, `bqsr_report` | Logical type |
| `component` | see below | Role within type |

### Component Values by Type

| type | component values |
|------|-----------------|
| `reference` | `fasta`, `faidx`, `sequence_dictionary`, `aligner_index` |
| `reads` | `r1`, `r2` |
| `alignment` | `alignment`, `index`, `markdup_metrics` |
| `variants` | `vcf`, `index` |
| `known_sites` | (none / `index`) |
| `bqsr_report` | (none) |

### Additional Metadata Fields

| Field | Applicable types | Values | Source |
|-------|-----------------|--------|--------|
| `build` | reference, variants | e.g., `GRCh38`, `GRCh37`, `T2T-CHM13` | Filename pattern or VCF/BAM header |
| `sample_id` | reads, alignment, variants | e.g., `NA12829` | Filename pattern or BAM @RG header |
| `aligner` | reference (aligner_index) | e.g., `bwa_index` | Extension pattern |
| `caller` | variants | e.g., `haplotypecaller` | Interactive (user-specified) |
| `name` | known_sites | original filename | Automatic |

## Inference Rules

### Phase 1: Extension-Based Type Detection (Automatic)

These are deterministic and require no user input:

```
.fa, .fasta               -> type=reference, component=fasta
.fa.fai, .fasta.fai       -> type=reference, component=faidx
.dict                     -> type=reference, component=sequence_dictionary
.fa.amb/.ann/.bwt/.pac/.sa -> type=reference, component=aligner_index, aligner=bwa_index
.fq.gz, .fastq.gz         -> type=reads (component from R1/R2 pattern)
.bam                      -> type=alignment, component=alignment
.bai, .bam.bai            -> type=alignment, component=index
.cram / .crai             -> type=alignment, component=alignment/index
.vcf, .vcf.gz             -> type=variants, component=vcf
.g.vcf, .g.vcf.gz         -> type=variants, component=vcf
.vcf.idx, .vcf.gz.tbi     -> type=variants, component=index
.table                    -> type=bqsr_report
_metrics.txt              -> type=alignment, component=markdup_metrics
```

### Phase 2: Filename Pattern Inference (Best-Effort)

These extract metadata when patterns match but do not fail on mismatch:

**Sample ID**: Leading segment before first region/target token. Regex: `^([A-Za-z0-9]+?)_` for reads, alignments, and variants.

**Build**: Leading segment for references. Recognized: `GRCh38`, `GRCh37`, `hg38`, `hg19`, `T2T-CHM13`, `CHM13`. Pattern: `^(GRCh3[78]|hg[13][89]|T2T-CHM13|CHM13)[_.]`.

**R1/R2**: Match `_R1` or `_R2` (case-insensitive) in filename stem.

**Known sites**: Files matching patterns like `Mills_and_1000G`, `dbsnp`, `hapmap`, `1000G_phase`, `Axiom_Exome` are tagged as `type=known_sites` with `name` set to the filename.

### Phase 3: File Content Introspection (On-Demand)

When filename patterns are insufficient, the tool can introspect file contents:

| Tool | What it reveals | When to use |
|------|----------------|-------------|
| `samtools view -H` | @RG sample_id, @SQ reference build, sort order | BAM/CRAM files missing sample_id or build |
| VCF header parser | ##reference, ##source (caller), #CHROM sample columns | VCF files missing build, caller, or sample_id |
| `samtools idxstats` | Reference contigs present | Confirming reference build |

### Phase 4: Interactive Resolution (MCP-Driven)

When the tool cannot determine a required field, it returns an ambiguity report to the LLM, which can:
1. Prompt the user for the missing value
2. Use introspection tools to resolve it
3. Skip the file

## Architecture

### Module Structure

```
src/stargazer/utils/fs_scanner.py     # Core scanner + classifier logic
src/stargazer/utils/introspection.py  # File content introspection helpers
```

### Core API

```python
@dataclass
class FileClassification:
    """Result of classifying a single file."""

    path: Path  # Absolute path
    rel_path: str  # Relative to scan root
    keyvalues: dict[str, str]  # Inferred metadata
    confidence: float  # 0.0-1.0, how confident the inference is
    missing_fields: list[str]  # Fields that could not be inferred
    ambiguities: list[str]  # Human-readable descriptions of uncertainties


class FilesystemScanner:
    """Scans a directory and infers keyvalues metadata for genomics files."""

    def classify(self, path: Path, root: Path) -> FileClassification | None:
        """Classify a single file. Returns None if unrecognizable."""

    def scan_directory(
        self, directory: Path, recursive: bool = True
    ) -> list[FileClassification]:
        """Scan all files in a directory and classify them.
        Returns classifications sorted by confidence (lowest first, so ambiguities surface early).
        """
```

### MCP Tool Interface

```python
async def scan_filesystem(directory: str) -> dict:
    """MCP tool: Scan a directory and return classification results.

    Returns:
        {
            "classified": [...],       # Files with complete metadata
            "needs_review": [...],     # Files with missing fields or ambiguities
            "skipped": [...],          # Unrecognizable files
        }
    """


async def introspect_file(path: str, method: str) -> dict:
    """MCP tool: Introspect a file's contents for metadata.

    Args:
        path: File to introspect
        method: "bam_header", "vcf_header", "idxstats"

    Returns:
        Parsed metadata from file contents.
    """


async def register_file(
    path: str,
    keyvalues: dict[str, str],
    db_path: str | None = None,
) -> dict:
    """MCP tool: Add a classified file to the local TinyDB.

    Called after the LLM has resolved all ambiguities for a file.
    """


async def commit_scan(
    directory: str,
    classifications: list[dict],
    db_path: str | None = None,
) -> dict:
    """MCP tool: Write all resolved classifications to TinyDB at once."""
```

### TinyDB Record Format

Matches `LocalStorageClient` format:

```python
{
    "id": f"local_{rel_path}",
    "cid": f"local_{rel_path}",
    "name": filename,
    "size": file_stat.st_size,
    "keyvalues": classified.keyvalues,
    "created_at": now_utc_iso,
    "is_public": False,
    "rel_path": rel_path,  # relative path from scan root, supports subdirs
}
```

### LocalStorageClient Changes

`download_file` path resolution must handle relative paths with subdirectories:

```python
# Current: self.local_dir / cid
# Updated: self.local_dir / record["rel_path"]
```

## Interactive Workflow (LLM Perspective)

1. User asks: "Index the files in /data/project_x"
2. LLM calls `scan_filesystem("/data/project_x")`
3. Tool returns classified, needs_review, and skipped lists
4. For `needs_review` files, LLM either:
   a. Calls `introspect_file()` to get more info
   b. Asks the user to clarify
5. LLM calls `commit_scan()` with all resolved classifications
6. Tool writes the TinyDB and confirms record count

## Implementation Steps

1. Implement `FilesystemScanner` with extension-based classification (Phase 1 + 2)
2. Implement `introspection.py` helpers wrapping samtools and VCF header parsing
3. Implement MCP tool handlers (`scan_filesystem`, `introspect_file`, `register_file`, `commit_scan`)
4. Update `LocalStorageClient` to resolve `rel_path` for subdirectory support
5. Write unit tests for classifier against existing fixtures (ground truth)
6. Write integration tests for full scan-to-DB pipeline
7. Replace `tests/fixtures/build_local_db.py` with thin wrapper calling scanner

## Edge Cases

| Scenario | Handling |
|----------|----------|
| Unrecognized extension (`.log`, `.md`) | Placed in `skipped` list |
| `.bam.bai` vs `.bai` | Both recognized as alignment index |
| `_metrics.txt` suffix | alignment/markdup_metrics |
| `.bqsr.table` | bqsr_report (`.table` ext + `bqsr` in stem) |
| Known sites with complex names | Matched by database name patterns; no sample_id extraction |
| Multiple samples in same directory | Each file classified independently |
| Nested subdirectories | Recursive scan, `rel_path` preserves directory structure |
| Pre-existing DB | Deleted and rebuilt from scratch |
| Symlinks | Followed (default `Path.stat()` behavior) |
| samtools not installed | Introspection tools degrade gracefully, report unavailability |
| Corrupt BAM/VCF headers | Introspection returns error, file goes to `needs_review` |
