## Type-Component Contract

### Overview

Stargazer types (Reference, Reads, Alignment, Variants) are **component containers**. Each component represents a specific file or group of files identified by the `component` keyvalue field.

**The Stronger Contract**: Instead of generic `add_files()` methods that accept any files, each type implements **named update methods** with **explicit parameter signatures**. This enforces metadata at the type system level, not just validation level.

```python
# Generic approach (old):
await alignment.add_files([path], keyvalues={"component": "alignment"})

# Named methods (new):
ipfile = await alignment.update_alignment(
    path, sample_id="S123", is_sorted=True
)  # Returns IpFile
ipfile = await alignment.update_index(path, sample_id="S123")  # Type-safe
```

### Core Principles

1. **IpFile is the interface**: All file operations go through IpFile objects
2. **Named update methods for uploads**: Each component has a specific update method (e.g., `update_alignment()`, `update_index()`) that accepts a Path and named metadata
3. **General hydration task**: A single `hydrate()` task routes IpFiles to types based on `type` and `component` keyvalues
4. **Explicit metadata via keyvalues**: Update methods accept named arguments that are converted to keyvalues, not dicts or kwargs
5. **Updates trigger uploads**: Update methods handle upload and return the IpFile
6. **Path resolution is explicit**: Download files to cache, then access via `ipfile.local_path`

### Component Structure

Each type defines its expected components as fields with specific update methods:

```python
@dataclass
class Reference:
    """Reference genome with various indices."""

    fasta: Optional[IpFile] = None
    faidx: Optional[IpFile] = None
    aligner_index: list[IpFile] = field(default_factory=list)  # Multi-file index

    # Metadata
    build: str  # e.g., "GRCh38"


@dataclass
class Alignment:
    """BAM/CRAM alignment with indices."""

    alignment: Optional[IpFile] = None  # BAM/CRAM file
    index: Optional[IpFile] = None  # BAI/CRAI file

    # Metadata
    sample_id: str
    is_sorted: bool = False
```

### Named Update Methods

**Every type implements specific update methods for each component.** These methods:
1. Accept a Path to upload
2. Accept named keyword arguments for metadata (no dicts)
3. Upload the file with keyvalues to IPFS (async)
4. Assign the IpFile to the component field
5. Return the resulting IpFile

#### Reference Update Methods

```python
class Reference:
    async def update_fasta(
        self,
        path: Path,
        build: Optional[str] = None,
    ) -> IpFile:
        """
        Upload reference FASTA component.

        Args:
            path: Path to file to upload
            build: Reference build (uses self.build if not provided)

        Returns:
            IpFile representing the uploaded file
        """
        from stargazer.utils.pinata import default_client

        ipfile = await default_client.upload_file(
            path,
            keyvalues={
                "type": "reference",
                "component": "fasta",
                "build": build or self.build,
            },
        )
        self.fasta = ipfile
        return self.fasta

    async def update_faidx(
        self,
        path: Path,
        build: Optional[str] = None,
    ) -> IpFile:
        """
        Upload FASTA index (.fai) component.

        Args:
            path: Path to file to upload
            build: Reference build (uses self.build if not provided)

        Returns:
            IpFile representing the uploaded file
        """
        from stargazer.utils.pinata import default_client

        ipfile = await default_client.upload_file(
            path,
            keyvalues={
                "type": "reference",
                "component": "faidx",
                "build": build or self.build,
            },
        )
        self.faidx = ipfile
        return self.faidx

    async def update_aligner_index(
        self,
        path: Path,
        aligner: str,
        build: Optional[str] = None,
    ) -> IpFile:
        """
        Upload aligner index component file.

        Call this method for each file in a multi-file index (e.g., BWA has
        .amb, .ann, .bwt, .pac, .sa files).

        Args:
            path: Path to index file to upload
            aligner: Aligner name (e.g., "bwa", "minimap2")
            build: Reference build (uses self.build if not provided)

        Returns:
            IpFile representing the uploaded index file
        """
        from stargazer.utils.pinata import default_client

        ipfile = await default_client.upload_file(
            path,
            keyvalues={
                "type": "reference",
                "component": "aligner_index",
                "aligner": aligner,
                "build": build or self.build,
            },
        )
        self.aligner_index.append(ipfile)
        return ipfile
```

#### Alignment Update Methods

```python
class Alignment:
    async def update_alignment(
        self,
        path: Path,
        format: Optional[str] = None,
        is_sorted: Optional[bool] = None,
    ) -> IpFile:
        """
        Upload alignment (BAM/CRAM) component.

        Args:
            path: Path to file to upload
            sample_id: Sample identifier (uses self.sample_id if not provided)
            is_sorted: Whether alignment is sorted (uses self.is_sorted if not provided)

        Returns:
            IpFile representing the uploaded file
        """
        from stargazer.utils.pinata import default_client

        ipfile = await default_client.upload_file(
            path,
            keyvalues={
                "type": "alignment",
                "component": "alignment",
                "format": format,
                "sample_id": self.sample_id,
                "is_sorted": str(
                    is_sorted if is_sorted is not None else self.is_sorted
                ),
            },
        )
        self.alignment = ipfile

        if is_sorted is not None:
            self.is_sorted = is_sorted

        return self.alignment

    async def update_index(
        self,
        path: Path,
        sample_id: Optional[str] = None,
    ) -> IpFile:
        """
        Upload alignment index (BAI/CRAI) component.

        Args:
            path: Path to file to upload
            sample_id: Sample identifier (uses self.sample_id if not provided)

        Returns:
            IpFile representing the uploaded file
        """
        from stargazer.utils.pinata import default_client

        ipfile = await default_client.upload_file(
            path,
            keyvalues={
                "type": "alignment",
                "component": "index",
                "sample_id": sample_id or self.sample_id,
            },
        )
        self.index = ipfile
        return self.index
```

### Hydration from IPFS

A general `hydrate` Flyte task queries IPFS and routes files to the appropriate types based on the `type` and `component` keyvalues. The strong contract between filesystem metadata and Python types allows this generalization.

```python
# Type registry maps (type, component) -> (TypeClass, field_name, is_list)
# is_list=True means the field is a list and files should be appended
TYPE_REGISTRY: dict[tuple[str, str], tuple[type, str, bool]] = {
    ("reference", "fasta"): (Reference, "fasta", False),
    ("reference", "faidx"): (Reference, "faidx", False),
    ("reference", "aligner_index"): (Reference, "aligner_index", True),
    ("alignment", "alignment"): (Alignment, "alignment", False),
    ("alignment", "index"): (Alignment, "index", False),
    ("variants", "vcf"): (Variants, "vcf", False),
    ("variants", "index"): (Variants, "index", False),
}

# Identity fields for each type (used to group files into instances)
TYPE_IDENTITY: dict[str, str] = {
    "reference": "build",
    "alignment": "sample_id",
    "variants": "sample_id",
}


@env.task
async def hydrate(
    filters: dict[str, str | list[str]],
) -> list[Reference | Alignment | Variants]:
    """
    Hydrate types from IPFS based on keyvalue filters.

    Uses cartesian product for list-valued filters. Routes each returned
    IpFile to the appropriate type based on its `type` and `component`
    keyvalues.

    Args:
        filters: Keyvalue filters (e.g., {"type": "alignment", "sample_id": ["S1", "S2"]})

    Returns:
        List of hydrated type instances
    """
    from stargazer.utils.pinata import default_client
    from stargazer.utils.query import generate_query_combinations

    # Generate cartesian product of queries
    query_combinations = generate_query_combinations(base_query={}, filters=filters)

    # Execute all queries and collect unique files
    all_files: dict[str, IpFile] = {}
    for query in query_combinations:
        ipfiles = await default_client.query_files(query)
        for ipfile in ipfiles:
            all_files[ipfile.cid] = ipfile

    # Group files by (type, identity_value)
    # e.g., ("alignment", "S123") -> [IpFile, IpFile]
    grouped: dict[tuple[str, str], list[IpFile]] = {}
    for ipfile in all_files.values():
        file_type = ipfile.keyvalues.get("type")
        if not file_type:
            continue

        identity_key = TYPE_IDENTITY.get(file_type)
        if not identity_key:
            continue

        identity_value = ipfile.keyvalues.get(identity_key)
        if not identity_value:
            continue

        key = (file_type, identity_value)
        grouped.setdefault(key, []).append(ipfile)

    # Build type instances from grouped files
    results: list[Reference | Alignment | Variants] = []
    for (file_type, identity_value), ipfiles in grouped.items():
        # Create instance with identity field
        identity_key = TYPE_IDENTITY[file_type]
        if file_type == "reference":
            instance = Reference(**{identity_key: identity_value})
        elif file_type == "alignment":
            instance = Alignment(**{identity_key: identity_value})
        elif file_type == "variants":
            instance = Variants(**{identity_key: identity_value})
        else:
            continue

        # Assign files to component fields
        for ipfile in ipfiles:
            component = ipfile.keyvalues.get("component")
            registry_key = (file_type, component)

            if registry_key in TYPE_REGISTRY:
                _, field_name, is_list = TYPE_REGISTRY[registry_key]
                if is_list:
                    getattr(instance, field_name).append(ipfile)
                else:
                    setattr(instance, field_name, ipfile)

        results.append(instance)

    return results
```

**Usage examples:**

```python
# Hydrate all alignments for a sample
alignments = await hydrate({"type": "alignment", "sample_id": "NA12878"})

# Hydrate reference with specific build
refs = await hydrate({"type": "reference", "build": "GRCh38"})

# Hydrate multiple samples (cartesian product)
alignments = await hydrate(
    {
        "type": "alignment",
        "sample_id": ["S1", "S2", "S3"],
    }
)

# Hydrate specific components only
refs = await hydrate(
    {
        "type": "reference",
        "build": "GRCh38",
        "component": ["fasta", "faidx"],
    }
)
```

### Workflow Pattern: Version Updates

As files flow through workflows, they often get updated (e.g., unsorted → sorted BAM). The pattern:

1. **Task produces new file** in cache directory
2. **Call named update method** with Path and explicit metadata arguments
3. **Upload happens inside the update method**
4. **Type instance is updated** with new IpFile

```python
@env.task
async def sort_bam(alignment: Alignment) -> Alignment:
    """Sort a BAM file."""
    from stargazer.utils.pinata import default_client

    # Download unsorted BAM to cache
    await default_client.download_file(alignment.alignment)
    unsorted_path = alignment.alignment.local_path

    # Sort in place (cache directory)
    sorted_path = default_client.cache_dir / f"{alignment.sample_id}.sorted.bam"
    subprocess.run(
        ["samtools", "sort", "-o", str(sorted_path), str(unsorted_path)],
        check=True,
        cwd=str(default_client.cache_dir),
    )

    # Update alignment with sorted BAM - upload happens automatically
    await alignment.update_alignment(
        sorted_path, sample_id=alignment.sample_id, is_sorted=True
    )

    return alignment


@env.task
async def index_bam(alignment: Alignment) -> Alignment:
    """Index a BAM file."""
    from stargazer.utils.pinata import default_client

    # Download BAM to cache
    await default_client.download_file(alignment.alignment)
    bam_path = alignment.alignment.local_path

    # Index in cache directory
    index_path = bam_path.with_suffix(bam_path.suffix + ".bai")
    subprocess.run(
        ["samtools", "index", str(bam_path), str(index_path)],
        check=True,
        cwd=str(default_client.cache_dir),
    )

    # Update index component - upload happens automatically
    await alignment.update_index(index_path, sample_id=alignment.sample_id)

    return alignment
```

### Path Resolution Pattern

Access files through components with explicit path resolution:

```python
# Download files to cache first
await default_client.download_file(reference.fasta)
await default_client.download_file(reads_r1)
await default_client.download_file(reads_r2)

# Get paths from IpFile local_path
fasta_path = reference.fasta.local_path
r1_path = reads_r1.local_path
r2_path = reads_r2.local_path

# Pass explicit paths to command
subprocess.run(
    ["bwa", "mem", str(fasta_path), str(r1_path), str(r2_path)],
    cwd=str(default_client.cache_dir),
    check=True,
)
```

### Command Execution Pattern

All commands run in the cache directory with explicit paths:

```python
from stargazer.utils.pinata import default_client

# Download files to cache
await default_client.download_file(reference.fasta)
await default_client.download_file(alignment.alignment)

# All file operations happen in cache_dir
subprocess.run(
    [
        "gatk",
        "HaplotypeCaller",
        "-R",
        str(reference.fasta.local_path),
        "-I",
        str(alignment.alignment.local_path),
        "-O",
        str(default_client.cache_dir / "output.vcf"),
    ],
    cwd=str(default_client.cache_dir),  # Always run in cache
    check=True,
)
```

### Benefits of This Contract

1. **Type safety**: Components are typed fields, not dict keys
2. **Explicit metadata**: Required fields enforced at IpFile creation via keyvalues
3. **Version tracking**: Each update creates a new CID with updated keyvalues
4. **Path clarity**: No ambiguity about where files live (always in cache after download)
5. **Cache efficiency**: Single directory simplifies management
6. **Async-first**: All upload/download operations are async for better performance

---