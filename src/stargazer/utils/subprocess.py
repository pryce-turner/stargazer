"""
### Subprocess utilities for running external commands.

spec: [docs/architecture/tasks.md](../architecture/tasks.md)
"""

import asyncio
import shlex
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from stargazer.config import logger


async def _run(
    cmd: Sequence[Any],
    cwd: Path | str | None = None,
) -> tuple[str, str]:
    """
    Run a command as a subprocess and capture output.

    Args:
        cmd: Command and arguments. Non-string types (Path, int, etc.) will be
             converted to strings automatically.
        cwd: Working directory for the command (optional)

    Returns:
        Tuple of (stdout, stderr) as strings

    Raises:
        RuntimeError: If the command exits with a non-zero return code
    """
    # Convert all command arguments to strings
    str_cmd = [str(arg) for arg in cmd]

    logger.info(f"Running: {shlex.join(str_cmd)}")

    # Run the subprocess
    process = await asyncio.create_subprocess_exec(
        *str_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd) if cwd else None,
    )

    stdout, stderr = await process.communicate()

    # Check return code and raise error if failed
    if process.returncode != 0:
        stdout_str = stdout.decode("utf-8").strip()
        stderr_str = stderr.decode("utf-8").strip()

        # Build error message with available output
        error_parts = [f"Command {str_cmd[0]} failed with code {process.returncode}:"]
        if stderr_str:
            error_parts.append(f"stderr:\n{stderr_str}")
        if stdout_str:
            error_parts.append(f"stdout:\n{stdout_str}")

        raise RuntimeError("\n".join(error_parts))

    return stdout.decode("utf-8"), stderr.decode("utf-8")
