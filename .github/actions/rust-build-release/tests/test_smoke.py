"""Smoke tests for rust-build-release action."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

if sys.platform == "win32":
    pytest.skip("bash unavailable on Windows runners", allow_module_level=True)


def run_script(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Execute *script* with Bash and return the completed process."""
    cmd = ["bash", str(script), *args]
    return subprocess.run(  # noqa: S603
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )


def test_runs_placeholder_script() -> None:
    """The placeholder build script warns and fails."""
    script = Path(__file__).resolve().parents[1] / "src" / "main.sh"
    res = run_script(script, "x86_64-unknown-linux-gnu")
    assert res.returncode != 0
    assert "is a stub" in res.stdout
