# Flatten BioTypes into Relational Components + Constellations

## Context

BioTypes (Reference, Alignment, Variants, Reads) serve as containers that group related Assets for clean task signatures. However, they create significant overhead: a manual `TYPE_REGISTRY` and `TYPE_IDENTITY` mapping in `hydrate.py`, introspection machinery in `biotype.py`, and awkward fits like `KnownSites` being shoehorned into a `Variants` container.

The goal is to flatten the type hierarchy so that **assets are the primary objects** with relational CID-based metadata linking them together, forming a queryable graph. **Constellations** are dynamic query-result namespaces — not predefined subclasses — that emerge from what a workflow needs. Workflows take minimal filter inputs (e.g. `build`, `sample_id`) and call `assemble()` internally to pull the right assets, keeping user-facing signatures clean while letting the workflow be explicit about its needs.

### Design Precedents

This architecture draws from several established systems:

- **IPLD (InterPlanetary Linked Data)** — The data model layer beneath IPFS. Every piece of data is a CID-addressed block, and blocks link to each other via CID references. Our assets-as-blocks + CID links pattern mirrors this directly — and since we already use Pinata/IPFS for storage, we're essentially building an IPLD-shaped layer. Unlike IPLD Schemas which predefine structure, our constellations are dynamic — closer to IPLD Selector traversals that resolve at query time.

- **Arvados Keep** — Content-addressable storage for bioinformatics. Files are identified by content hash and carry arbitrary key-value metadata properties. Metadata is separated from objects so the same data can be tagged differently for different projects. Our `keyvalues` dict on Asset follows this same principle. Arvados collections group files by shared properties — our `assemble(build="GRCh38")` is the same pattern.

- **W3C PROV** — The standard provenance model with three node types: entities (our assets), activities (our tasks), and agents (the tools). Adding `task_id` or `git_hash` as metadata fields maps directly to PROV's entity-activity relationships, providing built-in data lineage.

- **SPADE** — Models provenance as a property graph where content-based hashing produces unique identifiers for vertices and edges. Our `*_cid` keyvalue fields are property-graph edges where the edge label is the field name and the target is the CID.

## Scope

Types system only — no changes to tasks, workflows, or MCP server.

## Steps

### 1. Rename and redesign `ComponentFile` → `Asset` (`src/stargazer/assets/component.py` → `src/stargazer/assets/asset.py`)

**Changes:**
- Rename file and class: `ComponentFile` → `Asset`
- Rename `_component_key` → `_asset_key`
- Remove `_type_key` — assets are identified by `_asset_key` alone
- Change registry from `dict[tuple[str, str], type]` to `dict[str, type]` keyed by `_asset_key`
- Remove `type` from keyvalues auto-set in `__post_init__`; rename the `component` keyvalue to `asset`
- Everything else stays: `cid`, `path`, `keyvalues`, magic `__getattr__/__setattr__`, `update()`, `to_dict()`/`from_dict()`

Each asset is a node in a content-addressed DAG (per IPLD's data model). The `cid` is the node's address, `keyvalues` are its properties, and any `*_cid` field in keyvalues is a directed edge to another node.

### 2. Redefine concrete assets

Each file keeps its asset subclasses but drops the BioType class. Assets gain relational CID fields that create edges in the graph — these are set at upload/creation time by the tasks that produce them (per W3C PROV: the activity records which entities it consumed and produced).

**`reference.py`** — `Reference`, `ReferenceIndex`, `SequenceDict`, `AlignerIndex`
- Rename `ReferenceFile` → `Reference` (drop `File` — it's implicit in the Asset designation)
- Update `_asset_key` values: `Reference` → `"reference"`, `ReferenceIndex` → `"reference_index"`, `SequenceDict` → `"sequence_dict"`, `AlignerIndex` → `"aligner_index"`
- `ReferenceIndex`, `SequenceDict`, `AlignerIndex` gain an implicit relational field: tasks that produce them will set `reference_cid` linking back to the `Reference` they were built from
- `_field_defaults` keeps `build`

**`reads.py`** — `R1`, `R2`
- Rename `R1File` → `R1`, `R2File` → `R2`
- Each carries a `mate_cid` field pointing to its pair's CID (None for single-end reads)
- This makes reads follow the same CID-link pattern as every other asset — no special cases

**`alignment.py`** — `Alignment`, `AlignmentIndex`, `BQSRReport`, `DuplicateMetrics`
- Rename `AlignmentFile` → `Alignment` (drop `File`)
- `AlignmentIndex`, `BQSRReport`, `DuplicateMetrics` link via `alignment_cid`
- `Alignment` itself carries `reference_cid` and `r1_cid` for provenance (PROV entity-to-entity derivation)

**`variants.py`** — `Variants`, `VariantsIndex`, `KnownSites`
- Rename `VariantsFile` → `Variants` (drop `File`), `VariantsIndex` → `VariantsIndex` (already clean)
- `VariantsIndex` links via `variants_cid`
- `KnownSites` is a standalone asset with `build` and `source` fields — no container needed (like an Arvados collection that stands alone with its own metadata)

### 3. Create `Constellation` class and `assemble()` (`src/stargazer/assets/constellation.py`)

New file replacing `biotype.py`. A Constellation is a **dynamic query-result namespace**, not a base class for subclassing. It is populated by `assemble()` which queries storage, specializes results, and groups them by `_asset_key`.

```python
@dataclass
class Constellation:
    """Dynamic namespace of assets assembled from a storage query.

    Attributes are accessed by asset_key (e.g. .fasta, .faidx).
    Single results return the asset directly; multiple results
    return a list. Missing assets return None.
    """

    _assets: dict[str, Asset | list[Asset]]

    def __getattr__(self, name: str) -> Asset | list[Asset] | None: ...


async def assemble(**filters) -> Constellation:
    """Query storage by keyvalue filters, specialize results, return as Constellation.

    Wraps the cartesian product query + specialize pattern into a single call.
    Workflows call this at the top to gather what they need.

    Examples:
        ref = await assemble(build="GRCh38")
        ref.reference        # Reference instance
        ref.reference_index  # ReferenceIndex instance
        ref.aligner_index    # list[AlignerIndex]

        reads = await assemble(sample_id="NA12878", asset=["r1", "r2"])
        reads.r1        # R1 instance
        reads.r2        # R2 instance or None
    """
    ...
```

**How it works:**
1. `assemble(**filters)` calls the existing cartesian product query via storage client
2. Results are specialized via the asset registry
3. Assets are grouped by `_asset_key` into the Constellation namespace
4. Single matches → direct attribute, multiple matches → list, no match → None

**How workflows use it:**
```python
async def preprocess_sample(build: str, sample_id: str):
    # Workflow knows what it needs, assembles from minimal user inputs
    ref = await assemble(build=build)
    reads = await assemble(sample_id=sample_id, asset=["r1", "r2"])
    known = await assemble(asset="known_sites", build=build)

    # Typed access, workflow validates early
    fasta, faidx = ref.reference, ref.reference_index
    r1, r2 = reads.r1, reads.r2
    ...
```

The user-facing workflow signature is just `(build: str, sample_id: str)`. The workflow internally knows which assets it needs and uses targeted filters to assemble them. No predefined constellation schemas needed.

**fetch() on Constellation:**
Downloads all contained components to local cache, same as BioType.fetch() today.

### 4. Update `specialize()` and `__init__.py` (`src/stargazer/assets/__init__.py`)

- `ASSET_REGISTRY` becomes `dict[str, type[Asset]]` keyed by `_asset_key`
- `specialize()` looks up by `asset` keyvalue only (single key, not tuple)
- Update exports: remove `BioType`, add `Constellation` and `assemble`, rename asset classes

### 5. Delete `biotype.py`

No longer needed — `Constellation` replaces it entirely.

### 6. Replace `hydrate.py` (`src/stargazer/utils/hydrate.py`)

- Remove `TYPE_REGISTRY`, `TYPE_IDENTITY`, and the `hydrate()` function entirely
- `assemble()` in `constellation.py` replaces this — it's a simpler pipeline: query → specialize → namespace
- Cartesian product query logic (`query.py`) stays unchanged and is reused by `assemble()`

### 7. Update tests

**Delete:** `tests/types/test_serialization.py` (if it tests BioType serialization)

**Rewrite:**
- `tests/unit/test_component_file.py` → `tests/unit/test_asset.py` — rename, remove `type` keyvalue references
- `tests/unit/test_specialize.py` — single-key registry, new class names
- `tests/types/test_reference.py` — use `assemble()` + `Fasta` etc.
- `tests/types/test_reads.py` — use `assemble()` + `R1`/`R2`
- `tests/types/test_alignment.py` — use `assemble()` + asset classes
- `tests/types/test_variants.py` — use `assemble()` + `VCF`/`VCFIndex`

**New:**
- `tests/types/test_constellation.py` — test `Constellation` namespace access (single, list, missing), `assemble()` end-to-end with mock storage, filter narrowing with `asset=`

## Files Modified

| File | Action |
|------|--------|
| `src/stargazer/assets/component.py` → `src/stargazer/assets/asset.py` | **Rename** + edit (→ `Asset`, drop `_type_key`, single-key registry) |
| `src/stargazer/assets/constellation.py` | **Create** (replaces `biotype.py`) |
| `src/stargazer/assets/reference.py` | Edit (rename classes, drop BioType) |
| `src/stargazer/assets/reads.py` | Edit (rename classes, drop BioType) |
| `src/stargazer/assets/alignment.py` | Edit (drop BioType) |
| `src/stargazer/assets/variants.py` | Edit (rename classes, drop BioType) |
| `src/stargazer/assets/__init__.py` | Edit (new exports, single-key registry) |
| `src/stargazer/assets/biotype.py` | **Delete** |
| `src/stargazer/utils/hydrate.py` | **Delete** (replaced by `assemble()`) |
| `tests/unit/test_component_file.py` → `tests/unit/test_asset.py` | **Rename** + edit |
| `tests/unit/test_specialize.py` | Edit |
| `tests/types/test_reference.py` | Edit |
| `tests/types/test_reads.py` | Edit |
| `tests/types/test_alignment.py` | Edit |
| `tests/types/test_variants.py` | Edit |
| `tests/types/test_constellation.py` | **Create** |

## Verification

1. `uv run pytest tests/unit/test_asset.py tests/unit/test_specialize.py` — base class works
2. `uv run pytest tests/types/` — all type + constellation tests pass
3. `uv run ruff check src/stargazer/assets/` — no lint errors

## References

- [IPLD — The data model of the content-addressable web](https://ipld.io/)
- [IPLD Schemas](https://ipld.io/docs/schemas/) — typed structure definitions over CID-linked data
- [IPLD Selectors (specs)](https://github.com/ipld/specs) — declarative graph traversals
- [Arvados Keep — Content-Addressable Storage](https://dev.arvados.org/projects/arvados/wiki/Keep) — CAS with key-value metadata for bioinformatics
- [Arvados Collections API](https://doc.arvados.org/v2.1/api/methods/collections.html) — file grouping with searchable properties
- [W3C PROV — Data Provenance in Biomedical Research (JMIR)](https://www.jmir.org/2023/1/e42289/) — entity/activity/agent provenance model
- [SPADE — Digging into Big Provenance (CACM)](https://cacm.acm.org/practice/digging-into-big-provenance-with-spade/) — property graph provenance with content-based hashing
- [Provenance in bioinformatics workflows (BMC Bioinformatics)](https://link.springer.com/article/10.1186/1471-2105-14-S11-S6)
