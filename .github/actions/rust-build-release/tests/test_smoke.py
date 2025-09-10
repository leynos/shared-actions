"""Smoke tests for rust-build-release action."""

from __future__ import annotations

import subprocess
from pathlib import Path


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
    """The placeholder build script exits successfully."""
    script = Path(__file__).resolve().parents[1] / "src" / "main.sh"
    res = run_script(script)
    assert res.returncode == 0
    assert "not yet implemented" in res.stdout
