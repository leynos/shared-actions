"""Smoke tests for rust-build-release action."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from cmd_utils import run_cmd

if sys.platform == "win32":
    pytest.skip("cross build not supported on Windows runners", allow_module_level=True)


def run_script(
    script: Path, *args: str, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Execute *script* in *cwd* and return the completed process."""
    cmd = [str(script), *args]
    return subprocess.run(  # noqa: S603
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd,
    )


def test_action_builds_release_binary_and_manpage() -> None:
    """The build script produces a release binary and man page."""
    script = Path(__file__).resolve().parents[1] / "src" / "main.py"
    project_dir = Path(__file__).resolve().parents[4] / "rust-toy-app"
    run_cmd(["rustup", "toolchain", "install", "1.89.0"])
    res = run_script(script, "x86_64-unknown-linux-gnu", cwd=project_dir)
    assert res.returncode == 0
    binary = project_dir / "target/x86_64-unknown-linux-gnu/release/rust-toy-app"
    assert binary.exists()
    manpage_glob = project_dir.glob(
        "target/x86_64-unknown-linux-gnu/release/build/rust-toy-app-*/out/rust-toy-app.1"
    )
    assert any(manpage_glob)


def test_fails_without_target() -> None:
    """Script exits with an error when no target is provided."""
    script = Path(__file__).resolve().parents[1] / "src" / "main.py"
    project_dir = Path(__file__).resolve().parents[4] / "rust-toy-app"
    res = run_script(script, cwd=project_dir)
    assert res.returncode != 0
    assert "no build target specified" in res.stderr


def test_fails_for_invalid_toolchain() -> None:
    """Script surfaces rustup errors for invalid toolchains."""
    script = Path(__file__).resolve().parents[1] / "src" / "main.py"
    project_dir = Path(__file__).resolve().parents[4] / "rust-toy-app"
    res = run_script(
        script,
        "x86_64-unknown-linux-gnu",
        "--toolchain",
        "bogus",
        cwd=project_dir,
    )
    assert res.returncode != 0
    assert "toolchain 'bogus' is not installed" in res.stderr
