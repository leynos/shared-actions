"""Tests for copy_openbsd_stdlib.py script."""

from __future__ import annotations

import os
import typing as typ
from pathlib import Path

from plumbum import local

from cmd_utils import run_completed_process

if typ.TYPE_CHECKING:  # pragma: no cover - typing only
    import subprocess


def run_script(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Execute *script* using ``uv run --script`` and return the process."""
    command = local["uv"]["run", "--script", str(script)]
    if args:
        command = command[list(args)]
    merged = {**os.environ}
    root = str(Path(__file__).resolve().parents[4])
    current_pp = merged.get("PYTHONPATH", "")
    merged["PYTHONPATH"] = f"{root}{os.pathsep}{current_pp}" if current_pp else root
    merged["PYTHONIOENCODING"] = "utf-8"
    return run_completed_process(
        command,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=merged,
    )


def test_copy_success(tmp_path: Path) -> None:
    """Copying succeeds and preserves file contents."""
    artifact = tmp_path / "build" / "artifacts"
    artifact.mkdir(parents=True)
    (artifact / "foo.txt").write_text("hi")

    sysroot = tmp_path / "sysroot"

    script = Path(__file__).resolve().parents[1] / "scripts" / "copy_openbsd_stdlib.py"
    res = run_script(script, str(artifact), str(sysroot))

    assert res.returncode == 0
    dest = sysroot / "lib" / "rustlib" / "x86_64-unknown-openbsd" / "foo.txt"
    assert dest.read_text() == "hi"


def test_copy_overwrite(tmp_path: Path) -> None:
    """Existing destination files are overwritten."""
    artifact = tmp_path / "build" / "artifacts"
    artifact.mkdir(parents=True)
    (artifact / "new.txt").write_text("new")

    sysroot = tmp_path / "sysroot"
    dest = sysroot / "lib" / "rustlib" / "x86_64-unknown-openbsd"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "old.txt").write_text("old")

    script = Path(__file__).resolve().parents[1] / "scripts" / "copy_openbsd_stdlib.py"
    res = run_script(script, str(artifact), str(sysroot))

    assert res.returncode == 0
    assert not (dest / "old.txt").exists()
    assert (dest / "new.txt").read_text() == "new"


def test_copy_missing(tmp_path: Path) -> None:
    """Missing source directory exits with an error."""
    artifact = tmp_path / "missing"
    sysroot = tmp_path / "sysroot"

    script = Path(__file__).resolve().parents[1] / "scripts" / "copy_openbsd_stdlib.py"
    res = run_script(script, str(artifact), str(sysroot))

    assert res.returncode == 1
    assert "Error: Build artifacts not found" in res.stderr
