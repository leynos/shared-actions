"""Tests for validate_toml_versions.py."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "validate_toml_versions.py"


def _run(tmp_path: Path, *, version: str, fail_dynamic: str = "false") -> subprocess.CompletedProcess[str]:
    cmd = ["uv", "run", "--script", str(SCRIPT_PATH)]
    env = {**os.environ}
    root = str(Path(__file__).resolve().parents[4])
    env["PYTHONPATH"] = f"{root}{os.pathsep}{env.get('PYTHONPATH', '')}".rstrip(os.pathsep)
    env["PYTHONIOENCODING"] = "utf-8"
    env["RESOLVED_VERSION"] = version
    env["INPUT_TOML_GLOB"] = "**/pyproject.toml"
    env["INPUT_FAIL_ON_DYNAMIC_VERSION"] = fail_dynamic

    return subprocess.run(  # noqa: S603
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        cwd=tmp_path,
        env=env,
        check=False,
    )


def test_passes_when_versions_match(tmp_path: Path) -> None:
    project = tmp_path / "pkg"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        """
[project]
name = "demo"
version = "1.0.0"
""".strip()
    )

    result = _run(tmp_path, version="1.0.0")

    assert result.returncode == 0, result.stderr
    assert "all versions match 1.0.0" in result.stdout


def test_fails_on_mismatch(tmp_path: Path) -> None:
    project = tmp_path / "pkg"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        """
[project]
name = "demo"
version = "1.0.1"
""".strip()
    )

    result = _run(tmp_path, version="1.0.0")

    assert result.returncode == 1
    assert "version '1.0.1' != tag version '1.0.0'" in result.stderr


def test_dynamic_version_failure(tmp_path: Path) -> None:
    project = tmp_path / "pkg"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        """
[project]
name = "demo"
dynamic = ["version"]
""".strip()
    )

    result = _run(tmp_path, version="1.0.0", fail_dynamic="true")

    assert result.returncode == 1
    assert "dynamic 'version'" in result.stderr
