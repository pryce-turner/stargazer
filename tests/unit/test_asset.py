"""Tests for Asset base class."""

from pathlib import Path

from stargazer.assets.alignment import Alignment
from stargazer.assets.asset import Asset


class TestAssetRoundtrip:
    def test_roundtrip_with_path(self):
        original = Alignment(
            cid="Qm" + "a" * 44,
            path=Path("/tmp/test.bam"),
            sample_id="NA12878",
        )
        restored = Alignment.from_dict(original.to_dict())
        assert restored.cid == original.cid
        assert restored.path == original.path
        assert restored.sample_id == original.sample_id

    def test_roundtrip_without_path(self):
        original = Alignment(cid="Qm" + "b" * 44, sample_id="NA12878")
        restored = Alignment.from_dict(original.to_dict())
        assert restored.cid == original.cid
        assert restored.path is None
        assert restored.sample_id == original.sample_id

    def test_roundtrip_empty(self):
        original = Asset()
        restored = Asset.from_dict(original.to_dict())
        assert restored.cid == ""
        assert restored.path is None

    def test_to_dict_structure(self):
        asset = Alignment(cid="abc123", path=Path("/tmp/file.bam"), sample_id="S1")
        data = asset.to_dict()
        assert data["cid"] == "abc123"
        assert data["path"] == "/tmp/file.bam"
        assert data["keyvalues"]["sample_id"] == "S1"
        assert data["keyvalues"]["asset"] == "alignment"

    def test_to_dict_none_path(self):
        asset = Asset(cid="abc123")
        data = asset.to_dict()
        assert data["path"] is None

    def test_from_dict_round_trips_keyvalues(self):
        original = Alignment(cid="xyz", sample_id="S1", duplicates_marked=True)
        restored = Alignment.from_dict(original.to_dict())
        assert restored.sample_id == "S1"
        assert restored.duplicates_marked is True


class TestBareAssetKeyvalues:
    """Bare Asset carries free-form keyvalues verbatim (plan 20, piece 0)."""

    def test_to_keyvalues_passthrough(self):
        kv = {"asset": "sample_sheet", "sequencer": "novaseq", "lanes": "4"}
        assert Asset(keyvalues=kv).to_keyvalues() == kv

    def test_to_keyvalues_returns_copy(self):
        """Callers (e.g. _owner stamping) may mutate the result freely."""
        asset = Asset(keyvalues={"asset": "sample_sheet"})
        asset.to_keyvalues()["_owner"] = "someone"
        assert "_owner" not in asset.keyvalues

    def test_from_keyvalues_preserves(self):
        kv = {"asset": "sample_sheet", "sequencer": "novaseq"}
        restored = Asset.from_keyvalues(kv, cid="bafy123")
        assert restored.keyvalues == kv
        assert restored.cid == "bafy123"

    def test_to_dict_round_trip(self):
        original = Asset(
            cid="bafy456",
            path=Path("/tmp/sheet.tsv"),
            keyvalues={"asset": "sample_sheet", "sequencer": "novaseq"},
        )
        restored = Asset.from_dict(original.to_dict())
        assert restored.keyvalues == original.keyvalues
        assert restored.cid == original.cid
        assert restored.path == original.path

    def test_typed_subclass_ignores_keyvalues_field(self):
        """Typed assets serialize declared fields only; the inherited
        keyvalues dict never appears in their storage format."""
        alignment = Alignment(sample_id="NA12878")
        kv = alignment.to_keyvalues()
        assert "keyvalues" not in kv
        assert kv["asset"] == "alignment"

    def test_typed_from_keyvalues_ignores_keyvalues_key(self):
        restored = Alignment.from_keyvalues(
            {"asset": "alignment", "sample_id": "S1", "keyvalues": "{}"}
        )
        assert restored.sample_id == "S1"
        assert restored.keyvalues == {}


class TestAssetDefaults:
    def test_default_cid_is_empty(self):
        assert Asset().cid == ""

    def test_default_path_is_none(self):
        assert Asset().path is None

    def test_asset_key_in_to_keyvalues(self):
        """Concrete subclasses include 'asset' key in to_keyvalues()."""
        from stargazer.assets.reference import Reference

        ref = Reference()
        assert ref.to_keyvalues().get("asset") == "reference"

    def test_base_asset_to_keyvalues_empty(self):
        """Base Asset with no _asset_key returns empty dict from to_keyvalues()."""
        assert Asset().to_keyvalues() == {}
