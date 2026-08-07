"""Tests for the marshal module."""

from pathlib import Path

from stargazer.assets import Reference
from stargazer.marshal import marshal_output

# ---------------------------------------------------------------------------
# Output marshaling
# ---------------------------------------------------------------------------


def test_marshal_output_reference():
    """Reference with to_dict is serialized."""
    ref = Reference(build="GRCh38")
    result = marshal_output(ref)
    assert isinstance(result, dict)
    assert result["keyvalues"]["build"] == "GRCh38"


def test_marshal_output_path():
    result = marshal_output(Path("/tmp/file.txt"))
    assert result == "/tmp/file.txt"


def test_marshal_output_tuple():
    """Tuple becomes {"o0": ..., "o1": ...}."""
    result = marshal_output((Path("/a"), Path("/b")))
    assert result == {"o0": "/a", "o1": "/b"}


def test_marshal_output_list():
    refs = [Reference(build="A"), Reference(build="B")]
    result = marshal_output(refs)
    assert len(result) == 2
    assert result[0]["keyvalues"]["build"] == "A"


def test_marshal_output_none():
    assert marshal_output(None) is None


def test_marshal_output_primitive():
    assert marshal_output("hello") == "hello"
    assert marshal_output(42) == 42
