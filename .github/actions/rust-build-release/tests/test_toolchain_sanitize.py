"""Verify toolchain triple is sanitized for cross."""

from __future__ import annotations

import subprocess
import sys
import os
from pathlib import Path

import pytest

from cmd_utils import run_cmd

os.environ.setdefault("CROSS_CONTAINER_ENGINE", "docker")

if sys.platform == "win32":
    pytest.skip("cross build not supported on Windows runners", allow_module_level=True)


def run_script(
    script: Path, *args: str, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Execute *script* in *cwd* and return the completed process."""
    cmd = [str(script), *args]
    try:
        return subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            cwd=cwd,
            check=False,
        )
    except Exception as exc:  # pragma: no cover - defensive path
        return subprocess.CompletedProcess(cmd, 1, "", str(exc))


@pytest.mark.usefixtures("uncapture_if_verbose")
def test_accepts_toolchain_with_triple() -> None:
    """Running with a full toolchain triple succeeds."""
    script = Path(__file__).resolve().parents[1] / "src" / "main.py"
    project_dir = Path(__file__).resolve().parents[4] / "rust-toy-app"
    existing = subprocess.run(
        ["rustup", "toolchain", "list"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    if "1.89.0" not in existing:
        run_cmd(["rustup", "toolchain", "install", "1.89.0", "--profile", "minimal"])
    res = run_script(
        script,
        "x86_64-unknown-linux-gnu",
        "--toolchain",
        "1.89.0-x86_64-unknown-linux-gnu",
        cwd=project_dir,
    )
    assert res.returncode == 0
    binary = project_dir / "target/x86_64-unknown-linux-gnu/release/rust-toy-app"
    assert binary.exists()
    manpage_glob = project_dir.glob(
        "target/x86_64-unknown-linux-gnu/release/build/rust-toy-app-*/out/rust-toy-app.1"
    )
    assert any(manpage_glob)
