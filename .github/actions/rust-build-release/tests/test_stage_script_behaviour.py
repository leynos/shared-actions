"""Integration tests for the rust-build-release staging shell script."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ACTION = Path(__file__).resolve().parents[1] / "action.yml"
TARGET = "aarch64-unknown-linux-gnu"
BIN_NAME = "rust-toy-app"
DIST_MANPAGE = Path(f"dist/{BIN_NAME}_linux_arm64/{BIN_NAME}.1")
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or BASH is None,
    reason="staging script behaviour tests require a POSIX bash",
)


def _stage_script(
    tmp_path: Path,
    target: str = TARGET,
    bin_name: str = BIN_NAME,
) -> Path:
    data = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
    steps = data["runs"]["steps"]
    stage = next(step for step in steps if step.get("id") == "stage-artefacts")
    script = stage["run"]
    script = script.replace("${{ inputs.target }}", target)
    script = script.replace("${{ inputs.bin-name }}", bin_name)

    out = tmp_path / "github_output"
    out.write_text("", encoding="utf-8")
    script = f"export GITHUB_OUTPUT={out}\nexport target={target}\n" + script

    path = tmp_path / "stage.sh"
    path.write_text("#!/usr/bin/env bash\n" + script, encoding="utf-8")
    path.chmod(0o755)
    return path


def _write_file(path: Path, content: str = "content") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_binary(tmp_path: Path) -> None:
    _write_file(tmp_path / f"target/{TARGET}/release/{BIN_NAME}", "binary")


def _run_stage(script: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    return subprocess.run(  # noqa: S603,TID251 - exercise the action's bash fragment.
        [BASH, str(script)],
        cwd=cwd,
        check=False,
        text=True,
        capture_output=True,
    )


def test_stage_uses_stable_manpage_path_when_present(tmp_path: Path) -> None:
    """Use the generated-man path when the stable man page exists."""
    _write_binary(tmp_path)
    _write_file(
        tmp_path / f"target/generated-man/{TARGET}/release/{BIN_NAME}.1",
        "stable man page",
    )

    result = _run_stage(_stage_script(tmp_path), tmp_path)

    assert result.returncode == 0, result.stderr
    assert (tmp_path / DIST_MANPAGE).read_text(encoding="utf-8") == "stable man page"


def test_stage_uses_legacy_fallback_when_stable_path_absent(tmp_path: Path) -> None:
    """Use the legacy Cargo OUT_DIR man page when generated-man is absent."""
    _write_binary(tmp_path)
    _write_file(
        tmp_path / f"target/{TARGET}/release/build/{BIN_NAME}-abc123/out/{BIN_NAME}.1",
        "legacy man page",
    )

    result = _run_stage(_stage_script(tmp_path), tmp_path)

    assert result.returncode == 0, result.stderr
    assert (tmp_path / DIST_MANPAGE).read_text(encoding="utf-8") == "legacy man page"
    assert "::warning::stable man-page path" in result.stdout


def test_stage_errors_when_no_manpage_exists(tmp_path: Path) -> None:
    """Fail when neither stable nor legacy man page paths exist."""
    _write_binary(tmp_path)

    result = _run_stage(_stage_script(tmp_path), tmp_path)
    output = result.stdout + result.stderr

    assert result.returncode != 0
    assert "man page not found" in output


def test_stage_errors_when_multiple_legacy_matches_exist(tmp_path: Path) -> None:
    """Fail when the legacy fallback finds more than one matching man page."""
    _write_binary(tmp_path)
    for suffix in ("abc123", "def456"):
        _write_file(
            tmp_path
            / f"target/{TARGET}/release/build/{BIN_NAME}-{suffix}/out/{BIN_NAME}.1",
            f"legacy {suffix}",
        )

    result = _run_stage(_stage_script(tmp_path), tmp_path)
    output = result.stdout + result.stderr

    assert result.returncode != 0
    assert "expected exactly one" in output
