# Annotation-Based Field Declarations

## Goal

Replace the explicit `_field_types` / `_field_defaults` ClassVar dicts on every Asset
subclass with standard Python typed annotations:

```python
# Before
@dataclass
class Alignment(Asset):
    _asset_key: ClassVar[str] = "alignment"
    _field_types = {"duplicates_marked": bool, "bqsr_applied": bool}
    _field_defaults = {"sample_id": ""}


# After
@dataclass
class Alignment(Asset):
    _asset_key: ClassVar[str] = "alignment"
    sample_id: str = ""
    format: str = ""
    sorted: str = ""
    duplicates_marked: bool = False
    bqsr_applied: bool = False
    tool: str = ""
    reference_cid: str = ""
    r1_cid: str = ""
```

This also enforces that only declared fields are allowed in `keyvalues`.

---

## How It Works

`__init_subclass__` runs when the class body executes, **before** `@dataclass` processes
the class. At that point `cls.__annotations__` and `cls.__dict__` already hold the
annotation names and default values, so we can auto-build `_field_types` and
`_field_defaults` from them.

`@dataclass` then generates an `__init__` that includes declared fields as params.
`Asset.__setattr__` already routes non-own-attrs to `keyvalues`, so
`self.sample_id = "NA12878"` writes `keyvalues["sample_id"] = "NA12878"`.
`Asset.__getattr__` already reads from `keyvalues`, so `r1.sample_id` returns the value.
After `@dataclass` processes the class, it removes the class-level default attribute, so
instance access correctly falls through to `__getattr__`.

### Construction Conflict

The dataclass `__init__` always sets declared fields to their defaults (or passed values).
If a caller passes `keyvalues={"sample_id": "NA12878"}`, the subsequent
`self.sample_id = ""` overwrites it. Two construction sites use this pattern:
`from_dict` and `specialize`.

**Fix:** update both to extract declared fields from `keyvalues` and pass them as explicit
kwargs. The `keyvalues` param is no longer passed — it is rebuilt internally by
`__setattr__` as the dataclass `__init__` sets each field.

---

## Field Inventory (from task audit)

Every key written via `update()` or read via `keyvalues.get()` across all tasks.

### Reference
```
build: str = ""
```

### ReferenceIndex
```
build: str = ""
tool: str = ""
reference_cid: str = ""
```

### SequenceDict
```
build: str = ""
tool: str = ""
reference_cid: str = ""
```

### AlignerIndex
```
build: str = ""
aligner: str = ""
reference_cid: str = ""
```

### R1
```
sample_id: str = ""
mate_cid: str = ""
```

### R2
```
sample_id: str = ""
mate_cid: str = ""
```

### Alignment
Sources: bwa_mem, sort_sam, merge_bam_alignment, mark_duplicates, apply_bqsr
```
sample_id: str = ""
format: str = ""
sorted: str = ""
duplicates_marked: bool = False
bqsr_applied: bool = False
tool: str = ""
reference_cid: str = ""
r1_cid: str = ""
```

### AlignmentIndex
Sources: sort_sam, merge_bam_alignment, mark_duplicates
```
sample_id: str = ""
alignment_cid: str = ""
```

### BQSRReport
Source: base_recalibrator
```
sample_id: str = ""
tool: str = ""
alignment_cid: str = ""
```

### DuplicateMetrics
Source: mark_duplicates
```
sample_id: str = ""
tool: str = ""
alignment_cid: str = ""
```

### Variants
Sources: haplotype_caller, combine_gvcfs, joint_call_gvcfs, apply_vqsr
```
sample_id: str = ""
caller: str = ""
variant_type: str = ""
build: str = ""
vqsr_mode: str = ""
sample_count: int = 0
source_samples: list = None
```

### VariantsIndex
Sources: haplotype_caller, combine_gvcfs, joint_call_gvcfs, apply_vqsr
```
sample_id: str = ""
variants_cid: str = ""
```

### KnownSites
Read by variant_recalibrator via `r.keyvalues.get(...)`:
```
build: str = ""
resource_name: str = ""
known: str = "false"
training: str = "false"
truth: str = "false"
prior: str = "10"
```

### VQSRModel
Source: variant_recalibrator
```
sample_id: str = ""
mode: str = "SNP"
tranches_path: str = ""
build: str = ""
variants_cid: str = ""
```

---

## Changes

### 1. `src/stargazer/assets/asset.py`

**`__init_subclass__`** — after registering `_asset_key`, scan annotations:

```python
import typing

_MISSING = object()


def __init_subclass__(cls, **kwargs):
    super().__init_subclass__(**kwargs)
    ak = cls.__dict__.get("_asset_key", "")
    if ak:
        Asset._registry[ak] = cls

    field_types: dict[str, type] = {}
    field_defaults: dict[str, Any] = {}
    for name, annotation in cls.__dict__.get("__annotations__", {}).items():
        if name.startswith("_"):
            continue
        if typing.get_origin(annotation) is typing.ClassVar:
            continue
        if annotation is not str:
            field_types[name] = annotation
        default = cls.__dict__.get(name, _MISSING)
        if default is not _MISSING:
            field_defaults[name] = default

    if field_types:
        cls._field_types = field_types
    if field_defaults:
        cls._field_defaults = field_defaults
```

**`__setattr__`** — add key enforcement + fix `None` for list coercion:

```python
def __setattr__(self, name: str, value: Any) -> None:
    if name in self._own_attrs or name.startswith("_"):
        super().__setattr__(name, value)
        return
    # Enforce allowed keys — only on subclasses that declare _asset_key
    if self._asset_key:
        allowed = (
            frozenset(self._field_defaults) | frozenset(self._field_types) | {"asset"}
        )
        if name not in allowed:
            raise ValueError(
                f"{type(self).__name__} does not allow keyvalue '{name}'. "
                f"Allowed: {sorted(allowed)}"
            )
    # Coerce and store in keyvalues
    ftype = self._field_types.get(name)
    if isinstance(value, bool) or ftype is bool:
        self.keyvalues[name] = "true" if value else "false"
    elif ftype is list or isinstance(value, list):
        self.keyvalues[name] = ",".join(value) if value else ""
    else:
        self.keyvalues[name] = str(value)
```

Key differences from original plan:
- Guard uses `self._asset_key` (empty string on base `Asset`, truthy on subclasses)
- List branch handles `None` → `""` instead of `TypeError`

**`__post_init__`** — simplify to just set `"asset"`:

```python
def __post_init__(self):
    if self._asset_key:
        self.keyvalues.setdefault("asset", self._asset_key)
```

The `_field_defaults` loop is removed — the dataclass `__init__` already sets all
declared fields via `__setattr__`, which writes them to `keyvalues`.

**`from_dict`** — extract declared fields from keyvalues, pass as kwargs:

```python
@classmethod
def from_dict(cls, data: dict) -> Self:
    kv = data.get("keyvalues", {})
    declared = set(cls._field_defaults) | set(cls._field_types)
    field_kwargs = {k: v for k, v in kv.items() if k in declared}
    return cls(
        cid=data.get("cid", ""),
        path=Path(data["path"]) if data.get("path") else None,
        **field_kwargs,
    )
```

### 2. `src/stargazer/assets/__init__.py`

**`specialize`** — same unpacking pattern:

```python
def specialize(asset: Asset) -> Asset:
    key = asset.keyvalues.get("asset", "")
    cls = ASSET_REGISTRY.get(key)
    if cls is None:
        return asset
    declared = set(cls._field_defaults) | set(cls._field_types)
    field_kwargs = {k: v for k, v in asset.keyvalues.items() if k in declared}
    return cls(cid=asset.cid, path=asset.path, **field_kwargs)
```

### 3. `src/stargazer/server.py`

**`upload_file`** — construct the proper subclass instead of base `Asset`:

```python
@mcp.tool()
async def upload_file(path: str, keyvalues: dict[str, str]) -> dict:
    asset_key = keyvalues.get("asset")
    if asset_key not in ASSET_REGISTRY:
        valid = sorted(ASSET_REGISTRY.keys())
        raise ValueError(f"Invalid asset key {asset_key!r}. Valid keys: {valid}")
    cls = ASSET_REGISTRY[asset_key]
    declared = set(cls._field_defaults) | set(cls._field_types)
    field_kwargs = {k: v for k, v in keyvalues.items() if k in declared}
    comp = cls(path=Path(path), **field_kwargs)
    await default_client.upload(comp)
    return comp.to_dict()
```

This gives enforcement at the MCP boundary — unknown keys are silently dropped.
If we want strict rejection instead, add a check before construction:

```python
unknown = set(keyvalues) - declared - {"asset"}
if unknown:
    raise ValueError(
        f"Unknown keys for {asset_key}: {unknown}. Allowed: {sorted(declared)}"
    )
```

### 4. Asset Subclasses

All fields from the inventory above. Subclasses keep `@dataclass`.

**`reference.py`**
```python
@dataclass
class Reference(Asset):
    _asset_key: ClassVar[str] = "reference"
    build: str = ""


@dataclass
class ReferenceIndex(Asset):
    _asset_key: ClassVar[str] = "reference_index"
    build: str = ""
    tool: str = ""
    reference_cid: str = ""


@dataclass
class SequenceDict(Asset):
    _asset_key: ClassVar[str] = "sequence_dict"
    build: str = ""
    tool: str = ""
    reference_cid: str = ""


@dataclass
class AlignerIndex(Asset):
    _asset_key: ClassVar[str] = "aligner_index"
    build: str = ""
    aligner: str = ""
    reference_cid: str = ""
```

**`reads.py`**
```python
@dataclass
class R1(Asset):
    _asset_key: ClassVar[str] = "r1"
    sample_id: str = ""
    mate_cid: str = ""


@dataclass
class R2(Asset):
    _asset_key: ClassVar[str] = "r2"
    sample_id: str = ""
    mate_cid: str = ""
```

**`alignment.py`**
```python
@dataclass
class Alignment(Asset):
    _asset_key: ClassVar[str] = "alignment"
    sample_id: str = ""
    format: str = ""
    sorted: str = ""
    duplicates_marked: bool = False
    bqsr_applied: bool = False
    tool: str = ""
    reference_cid: str = ""
    r1_cid: str = ""


@dataclass
class AlignmentIndex(Asset):
    _asset_key: ClassVar[str] = "alignment_index"
    sample_id: str = ""
    alignment_cid: str = ""


@dataclass
class BQSRReport(Asset):
    _asset_key: ClassVar[str] = "bqsr_report"
    sample_id: str = ""
    tool: str = ""
    alignment_cid: str = ""


@dataclass
class DuplicateMetrics(Asset):
    _asset_key: ClassVar[str] = "duplicate_metrics"
    sample_id: str = ""
    tool: str = ""
    alignment_cid: str = ""
```

**`variants.py`**
```python
@dataclass
class Variants(Asset):
    _asset_key: ClassVar[str] = "variants"
    sample_id: str = ""
    caller: str = ""
    variant_type: str = ""
    build: str = ""
    vqsr_mode: str = ""
    sample_count: int = 0
    source_samples: list = None


@dataclass
class VariantsIndex(Asset):
    _asset_key: ClassVar[str] = "variants_index"
    sample_id: str = ""
    variants_cid: str = ""


@dataclass
class KnownSites(Asset):
    _asset_key: ClassVar[str] = "known_sites"
    build: str = ""
    resource_name: str = ""
    known: str = "false"
    training: str = "false"
    truth: str = "false"
    prior: str = "10"


@dataclass
class VQSRModel(Asset):
    _asset_key: ClassVar[str] = "vqsr_model"
    sample_id: str = ""
    mode: str = "SNP"
    tranches_path: str = ""
    build: str = ""
    variants_cid: str = ""
```

---

## Tests

New file `tests/unit/test_annotation_fields.py`. Write before implementing.

**Auto-derivation:**
- `R1._field_defaults == {"sample_id": "", "mate_cid": ""}`
- `R1._field_types == {}` (all str)
- `Alignment._field_types == {"duplicates_marked": bool, "bqsr_applied": bool}`
- `Alignment._field_defaults` contains all 8 fields
- `Variants._field_types == {"sample_count": int, "source_samples": list}`

**Enforcement:**
- `r1 = R1(); r1.unknown = "x"` → `ValueError`
- `Asset(keyvalues={"anything": "goes"})` → no error (base Asset unrestricted)
- `a = Alignment(); a.tool = "bwa"` → `a.keyvalues["tool"] == "bwa"` (allowed, works)

**Coercion:**
- `a = Alignment(duplicates_marked=True)` → `a.keyvalues["duplicates_marked"] == "true"`
- `a.duplicates_marked` → `True`
- `v = Variants(sample_count=3)` → `v.keyvalues["sample_count"] == "3"`
- `v.sample_count` → `3`
- `v = Variants(source_samples=None)` → `v.keyvalues["source_samples"] == ""`
- `v = Variants(source_samples=["A", "B"])` → `v.keyvalues["source_samples"] == "A,B"`

**Construction:**
- `R1(sample_id="NA12878").keyvalues["sample_id"] == "NA12878"`
- `R1(sample_id="NA12878").sample_id == "NA12878"`
- `R1().keyvalues["asset"] == "r1"` (auto-set)

**Round-trip:**
- `r1 = R1(sample_id="NA12878"); assert R1.from_dict(r1.to_dict()).sample_id == "NA12878"`
- `specialize(Asset(keyvalues={"asset": "r1", "sample_id": "NA12878"})).sample_id == "NA12878"`

---

## Implementation Steps

- [ ] Write `tests/unit/test_annotation_fields.py` — run, confirm failures
- [ ] Update `Asset.__init_subclass__` — auto-derive from annotations
- [ ] Update `Asset.__setattr__` — key enforcement + `None` guard on list branch
- [ ] Simplify `Asset.__post_init__` — remove `_field_defaults` loop
- [ ] Update `Asset.from_dict` — unpack keyvalues into kwargs
- [ ] Update `specialize` in `types/__init__.py` — same unpacking
- [ ] Update `upload_file` in `server.py` — construct subclass, enforce keys
- [ ] Update `reference.py` — annotation syntax per inventory
- [ ] Update `reads.py` — annotation syntax per inventory
- [ ] Update `alignment.py` — annotation syntax per inventory
- [ ] Update `variants.py` — annotation syntax per inventory
- [ ] `uv run pytest tests/unit/test_annotation_fields.py -v` — all pass
- [ ] `uv run pytest tests/ -v` — full suite passes
- [ ] `ruff --fix src/stargazer/assets/ src/stargazer/server.py`
