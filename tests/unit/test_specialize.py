"""Tests for Asset specialization."""

from pathlib import Path

from stargazer.assets import specialize
from stargazer.assets.alignment import Alignment, AlignmentIndex
from stargazer.assets.asset import Asset
from stargazer.assets.reads import R1, R2
from stargazer.assets.reference import (
    AlignerIndex,
    Reference,
    ReferenceIndex,
    SequenceDict,
)
from stargazer.assets.variants import Variants, VariantsIndex


def record(cid="Qm", path=None, **kv) -> dict:
    """Build a raw storage record for specialize()."""
    return {"cid": cid, "path": path, "keyvalues": kv}


class TestSpecialize:
    """specialize() converts a raw storage record to the correct derived type."""

    def test_reference(self):
        result = specialize(
            record("Qmfasta", Path("/tmp/ref.fa"), asset="reference", build="GRCh38")
        )
        assert type(result) is Reference
        assert result.cid == "Qmfasta"
        assert result.path == Path("/tmp/ref.fa")
        assert result.build == "GRCh38"

    def test_reference_index(self):
        result = specialize(record("Qmfaidx", asset="reference_index", build="T2T"))
        assert type(result) is ReferenceIndex
        assert result.build == "T2T"

    def test_sequence_dict(self):
        result = specialize(record("Qmdict", asset="sequence_dict"))
        assert type(result) is SequenceDict

    def test_aligner_index(self):
        result = specialize(record("Qmaln", asset="aligner_index", aligner="bwa"))
        assert type(result) is AlignerIndex
        assert result.aligner == "bwa"

    def test_alignment(self):
        result = specialize(
            record(
                "Qmbam",
                asset="alignment",
                sample_id="NA12878",
                duplicates_marked="true",
                bqsr_applied="false",
            )
        )
        assert type(result) is Alignment
        assert result.sample_id == "NA12878"
        assert result.duplicates_marked is True
        assert result.bqsr_applied is False

    def test_alignment_index(self):
        result = specialize(record("Qmbai", asset="alignment_index", sample_id="S1"))
        assert type(result) is AlignmentIndex

    def test_variants(self):
        result = specialize(
            record(
                "Qmvcf",
                asset="variants",
                sample_id="S1",
                sample_count="3",
                source_samples='["S1", "S2", "S3"]',
            )
        )
        assert type(result) is Variants
        assert result.sample_count == 3
        assert result.source_samples == ["S1", "S2", "S3"]

    def test_variants_index(self):
        result = specialize(record("Qmtbi", asset="variants_index"))
        assert type(result) is VariantsIndex

    def test_r1(self):
        result = specialize(record("Qmr1", asset="r1", sample_id="S1"))
        assert type(result) is R1

    def test_r2(self):
        result = specialize(record("Qmr2", asset="r2", sample_id="S1"))
        assert type(result) is R2

    def test_unknown_returns_base(self):
        result = specialize(record("Qmunk", asset="unknown_thing"))
        assert type(result) is Asset

    def test_unknown_preserves_keyvalues(self):
        """A custom-key record keeps its metadata when no class is registered
        in this process (plan 20: registry is import-driven, per-process).

        The key must never collide with a registered class — even the
        tutorials register real classes (e.g. sample_sheet) on import.
        """
        result = specialize(
            record("Qmunk", asset="never_registered_key", sequencer="novaseq")
        )
        assert type(result) is Asset
        assert result.keyvalues == {
            "asset": "never_registered_key",
            "sequencer": "novaseq",
        }

    def test_missing_asset_key_returns_base(self):
        result = specialize(record("Qmbare"))
        assert type(result) is Asset

    def test_malformed_value_falls_back_to_base(self):
        """A registered-key record whose non-str field doesn't json-parse
        degrades to a bare Asset with keyvalues intact — strict at upload,
        graceful at query. It must not raise."""
        result = specialize(
            record("Qmbad", asset="variants", sample_id="S1", sample_count="three")
        )
        assert type(result) is Asset
        assert result.cid == "Qmbad"
        assert result.keyvalues == {
            "asset": "variants",
            "sample_id": "S1",
            "sample_count": "three",
        }

    def test_unknown_keyvalues_dropped_on_specialize(self):
        """Undeclared keyvalues are silently dropped during specialization."""
        result = specialize(
            record(
                "Qm123",
                asset="alignment",
                sample_id="NA12878",
                custom_field="custom_value",
            )
        )
        assert result.sample_id == "NA12878"
        assert not hasattr(result, "custom_field")
