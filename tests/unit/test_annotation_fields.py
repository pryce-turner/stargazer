"""
Tests for field declarations and storage serialization on Asset subclasses.
"""

import pytest

from stargazer.assets import specialize
from stargazer.assets.alignment import Alignment
from stargazer.assets.asset import Asset
from stargazer.assets.reads import R1
from stargazer.assets.variants import Variants

# ---------------------------------------------------------------------------
# Normal attribute access
# ---------------------------------------------------------------------------


def test_r1_field_defaults():
    r1 = R1()
    assert r1.sample_id == ""
    assert r1.mate_cid == ""
    assert r1.sequencing_platform == ""


def test_alignment_bool_field_default():
    a = Alignment()
    assert a.duplicates_marked is False
    assert a.bqsr_applied is False


def test_variants_int_field_default():
    assert Variants().sample_count == 0


# ---------------------------------------------------------------------------
# Key enforcement
# ---------------------------------------------------------------------------


def test_unknown_key_raises_on_subclass():
    r1 = R1()
    with pytest.raises(AttributeError, match="has no field"):
        r1.unknown = "x"


def test_base_asset_allows_any_key():
    a = Asset()
    a.other_key = "also fine"


def test_allowed_key_works():
    a = Alignment()
    a.tool = "bwa"
    assert a.tool == "bwa"


# ---------------------------------------------------------------------------
# to_keyvalues serialization
# ---------------------------------------------------------------------------


def test_bool_true_serialized():
    assert (
        Alignment(duplicates_marked=True).to_keyvalues()["duplicates_marked"] == "true"
    )


def test_bool_false_serialized():
    assert (
        Alignment(duplicates_marked=False).to_keyvalues()["duplicates_marked"]
        == "false"
    )


def test_bool_read_back():
    a = Alignment(duplicates_marked=True)
    assert a.duplicates_marked is True


def test_int_serialized():
    assert Variants(sample_count=3).to_keyvalues()["sample_count"] == "3"


def test_int_read_back():
    assert Variants(sample_count=3).sample_count == 3


def test_list_serialized():
    assert (
        Variants(source_samples=["A", "B"]).to_keyvalues()["source_samples"]
        == '["A", "B"]'
    )


def test_list_read_back():
    assert Variants(source_samples=["A", "B"]).source_samples == ["A", "B"]


def test_asset_key_attr():
    assert R1()._asset_key == "r1"


def test_str_field_attr():
    assert R1(sample_id="NA12878").sample_id == "NA12878"


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_r1_round_trip():
    r1 = R1(sample_id="NA12878")
    assert R1.from_dict(r1.to_dict()).sample_id == "NA12878"


def test_alignment_bool_round_trip():
    a = Alignment(duplicates_marked=True)
    assert Alignment.from_dict(a.to_dict()).duplicates_marked is True


def test_specialize_preserves_fields():
    s = specialize({"cid": "", "keyvalues": {"asset": "r1", "sample_id": "NA12878"}})
    assert s.sample_id == "NA12878"
