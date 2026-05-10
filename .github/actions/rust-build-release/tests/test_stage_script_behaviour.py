"""Integration tests for the staging shell-script fragment in action.yml."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ACTION_YML = Path(__file__).resolve().parents[1] / "action.yml"

_TARGET = "aarch64-unknown-linux-gnu"
_BIN = "rust-toy-app"
_EXPECTED_MAN_PATH_OUTPUT = "man-path=dist/rust-toy-app_linux_arm64/rust-toy-app.1"


def _requires_bash() -> str:
    """Skip the test on platforms where bash is not a POSIX-compatible shell."""
    if sys.platform == "win32":
        pytest.skip("bash integration tests are not supported on Windows")
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not found on PATH")
    return bash


def _extract_stage_script(target: str = _TARGET, bin_name: str = _BIN) -> str:
    """Extract and parametrise the 'Stage artefacts' run block from action.yml."""
    data = yaml.safe_load(ACTION_YML.read_text(encoding="utf-8"))
    steps = data["runs"]["steps"]
    stage = next(s for s in steps if s.get("id") == "stage-artefacts")
    script: str = stage["run"]
    return (
        script.replace("${{ inputs.target }}", target)
        .replace("${{ inputs.bin-name }}", bin_name)
        .replace("${{ inputs.skip-man-page-discovery }}", "${skip_man_page_discovery}")
    )


def _write_stage_script(
    tmp_path: Path,
    target: str = _TARGET,
    bin_name: str = _BIN,
    *,
    skip_man_page_discovery: bool = False,
) -> Path:
    """Write an executable stage.sh into *tmp_path* and return its path."""
    gh_output = tmp_path / "github_output"
    gh_output.write_text("", encoding="utf-8")
    skip_value = "true" if skip_man_page_discovery else "false"
    script_body = (
        "#!/usr/bin/env bash\n"
        f'export GITHUB_OUTPUT="{gh_output}"\n'
        f'export target="{target}"\n'
        f'export skip_man_page_discovery="{skip_value}"\n'
        + _extract_stage_script(target, bin_name)
    )
    stage = tmp_path / "stage.sh"
    stage.write_text(script_body, encoding="utf-8")
    stage.chmod(0o755)
    return stage


def _stub_binary(project: Path, target: str = _TARGET, bin_name: str = _BIN) -> Path:
    """Create a zero-byte stub binary at the expected release path."""
    binary = project / f"target/{target}/release/{bin_name}"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(b"")
    return binary


def _stable_manpage(
    project: Path,
    target: str = _TARGET,
    bin_name: str = _BIN,
) -> Path:
    """Return the stable man-page path (does not create the file)."""
    return project / f"target/generated-man/{target}/release/{bin_name}.1"


def _legacy_manpage(
    project: Path,
    target: str = _TARGET,
    bin_name: str = _BIN,
    hash_suffix: str = "abc123",
) -> Path:
    """Return a legacy man-page path (does not create the file)."""
    return (
        project
        / f"target/{target}/release/build/{bin_name}-{hash_suffix}/out/{bin_name}.1"
    )


def _prepare_project(tmp_path: Path) -> tuple[str, Path, Path]:
    """Set up a project directory with a stub binary and staging script.

    Returns a *(bash, project, stage)* triple ready for a test to populate
    man-page fixtures and then call :func:`_run_stage`.
    """
    bash = _requires_bash()
    project = tmp_path / "project"
    project.mkdir()
    _stub_binary(project)
    stage = _write_stage_script(tmp_path)
    return bash, project, stage


def _prepare_project_with_skip(tmp_path: Path) -> tuple[str, Path, Path]:
    """Set up a project directory with man-page discovery disabled."""
    bash = _requires_bash()
    project = tmp_path / "project"
    project.mkdir()
    _stub_binary(project)
    stage = _write_stage_script(tmp_path, skip_man_page_discovery=True)
    return bash, project, stage


def _run_stage(
    bash: str,
    stage: Path,
    project: Path,
) -> subprocess.CompletedProcess[str]:
    """Execute the staging script and return the completed-process result."""
    return subprocess.run(  # noqa: S603,TID251 - exercise the bash fragment.
        [bash, str(stage)],
        cwd=project,
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_stable_path_used_when_present(tmp_path: Path) -> None:
    """Script succeeds and stages the man page from the stable generated-man path."""
    bash, project, stage = _prepare_project(tmp_path)
    man = _stable_manpage(project)
    man.parent.mkdir(parents=True, exist_ok=True)
    man.write_bytes(b".TH RUST-TOY-APP 1\n")

    result = _run_stage(bash, stage, project)

    assert result.returncode == 0, result.stderr
    assert len(list((project / "dist").rglob(f"{_BIN}.1"))) == 1
    github_output = (tmp_path / "github_output").read_text(encoding="utf-8")
    assert github_output.splitlines() == [_EXPECTED_MAN_PATH_OUTPUT]
    # Must not emit a warning when the stable path is used.
    assert "::warning::" not in result.stdout


def test_legacy_fallback_used_when_stable_absent(tmp_path: Path) -> None:
    """Script falls back to the legacy Cargo build path and emits a warning."""
    bash, project, stage = _prepare_project(tmp_path)
    legacy = _legacy_manpage(project)
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_bytes(b".TH RUST-TOY-APP 1\n")

    result = _run_stage(bash, stage, project)

    assert result.returncode == 0, result.stderr
    assert len(list((project / "dist").rglob(f"{_BIN}.1"))) == 1
    github_output = (tmp_path / "github_output").read_text(encoding="utf-8")
    assert github_output.splitlines() == [_EXPECTED_MAN_PATH_OUTPUT]
    assert "::warning::" in result.stdout
    assert (
        "target/generated-man/aarch64-unknown-linux-gnu/release/rust-toy-app.1"
        in result.stdout
    )


def test_error_when_no_manpage(tmp_path: Path) -> None:
    """Script exits non-zero when neither the stable nor the legacy path exists."""
    bash, project, stage = _prepare_project(tmp_path)

    result = _run_stage(bash, stage, project)

    assert result.returncode != 0
    assert "man page not found" in result.stdout


def test_error_when_multiple_legacy_matches(tmp_path: Path) -> None:
    """Script exits non-zero when multiple legacy man pages are found."""
    bash, project, stage = _prepare_project(tmp_path)
    for suffix in ("aaa111", "bbb222"):
        legacy = _legacy_manpage(project, hash_suffix=suffix)
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_bytes(b".TH RUST-TOY-APP 1\n")

    result = _run_stage(bash, stage, project)

    assert result.returncode != 0
    assert "expected exactly one" in result.stdout


def test_skip_manpage_discovery_stages_binary_without_manpage(tmp_path: Path) -> None:
    """Opt-out skips man-page lookup and stages only the release binary."""
    bash, project, stage = _prepare_project_with_skip(tmp_path)

    result = _run_stage(bash, stage, project)

    assert result.returncode == 0, result.stderr
    assert (project / f"dist/{_BIN}_linux_arm64/{_BIN}").exists()
    assert not list((project / "dist").rglob(f"{_BIN}.1"))
    assert "man page not found" not in result.stdout
    assert "man-path=" not in (tmp_path / "github_output").read_text(encoding="utf-8")


def test_skip_manpage_discovery_emits_notice(tmp_path: Path) -> None:
    """Opt-out emits a notice explaining that man-page outputs are suppressed."""
    bash, project, stage = _prepare_project_with_skip(tmp_path)

    result = _run_stage(bash, stage, project)

    assert result.returncode == 0, result.stderr
    assert (
        "::notice::skip-man-page-discovery is set; man-page discovery and the "
        "man-path output are suppressed for this run." in result.stdout
    )
