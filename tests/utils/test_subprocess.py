"""
Tests for subprocess utilities.
"""

import tempfile
from pathlib import Path

import pytest

from stargazer.utils.subprocess import _run


@pytest.mark.asyncio
async def test_run_command_basic():
    """Test basic command execution with string arguments."""
    stdout, stderr = await _run(["echo", "hello world"])
    assert stdout.strip() == "hello world"
    assert stderr == ""


@pytest.mark.asyncio
async def test_run_command_with_path_objects():
    """Test command execution with Path objects."""
    # Create a temporary file to test with
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
        temp_path = Path(f.name)
        f.write("test content\n")

    try:
        # Use cat command with Path object
        stdout, stderr = await _run(["cat", temp_path])
        assert stdout == "test content\n"
        assert stderr == ""
    finally:
        temp_path.unlink()


@pytest.mark.asyncio
async def test_run_command_with_integers():
    """Test command execution with integer arguments."""
    # Use head with an integer argument
    stdout, stderr = await _run(["echo", "-n", 123])
    assert stdout == "123"
    assert stderr == ""


@pytest.mark.asyncio
async def test_run_command_with_mixed_types():
    """Test command execution with mixed argument types."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        test_file = tmpdir_path / "test.txt"
        test_file.write_text("line 1\nline 2\nline 3\n")

        # Mix Path, string, and int
        stdout, stderr = await _run(["head", "-n", 2, test_file])
        assert stdout == "line 1\nline 2\n"
        assert stderr == ""


@pytest.mark.asyncio
async def test_run_command_with_cwd():
    """Test command execution with working directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        test_file = tmpdir_path / "testfile.txt"
        test_file.write_text("content\n")

        # Run ls in the temp directory - should list our file
        stdout, stderr = await _run(["ls"], cwd=tmpdir_path)
        assert "testfile.txt" in stdout
        assert stderr == ""

        # Test with cwd as string
        stdout, stderr = await _run(["ls"], cwd=str(tmpdir_path))
        assert "testfile.txt" in stdout
        assert stderr == ""


@pytest.mark.asyncio
async def test_run_command_failure():
    """Test that command failure raises RuntimeError with proper message."""
    # Run a command that will fail
    with pytest.raises(RuntimeError) as exc_info:
        await _run(["ls", "/nonexistent/directory/that/does/not/exist"])

    # Verify error message contains return code
    error_msg = str(exc_info.value)
    assert "failed with code" in error_msg
    # Verify stderr is included in error message
    assert "cannot access" in error_msg or "No such file" in error_msg


@pytest.mark.asyncio
async def test_run_command_returns_stderr():
    """Test that stderr is captured even on success."""
    # Some commands write to stderr even on success
    # Using 'ls' with warnings or a command that explicitly writes to stderr
    stdout, stderr = await _run(["sh", "-c", "echo hello; echo error >&2"])
    assert "hello" in stdout
    assert "error" in stderr


@pytest.mark.asyncio
async def test_run_command_empty_output():
    """Test command with no output."""
    stdout, stderr = await _run(["true"])
    assert stdout == ""
    assert stderr == ""


@pytest.mark.asyncio
async def test_run_command_path_in_command_name():
    """Test that Path can be used for the command itself."""
    # Find the actual path to 'echo' command
    which_stdout, _ = await _run(["which", "echo"])
    echo_path = Path(which_stdout.strip())

    # Use Path object for the command name
    stdout, stderr = await _run([echo_path, "test"])
    assert stdout.strip() == "test"
    assert stderr == ""
