"""Behavioural tests for the install-nixie composite action."""

from __future__ import annotations

import os
import shutil
import subprocess
import typing as typ
from dataclasses import dataclass  # noqa: ICN003 - requested direct import.
from pathlib import Path

import pytest
import yaml

ACTION_PATH = Path(__file__).resolve().parents[1] / "action.yml"


@dataclass(frozen=True)
class VersionOverrideCase:
    """Describe one version-override installation scenario."""

    binstall_available: bool
    merman_version: str
    nixie_version: str
    python_version: str
    expected_cargo_call: str


def _load_action() -> dict[str, typ.Any]:
    """Load the install-nixie action manifest."""
    return yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))


def _install_script() -> str:
    """Return the action's installation shell fragment."""
    steps = _load_action()["runs"]["steps"]
    assert len(steps) == 1, "install-nixie should have one atomic install step"
    run_script = steps[0].get("run")
    assert isinstance(run_script, str), "install step must define a shell script"
    return run_script


def _write_executable(path: Path, content: str) -> None:
    """Write an executable command stub."""
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


@dataclass(frozen=True)
class _InstallScriptOptions:
    binstall_available: bool
    include_cargo: bool = True
    include_uv: bool = True
    merman_version: str = "0.7.0"
    nixie_version: str = "1.1.0"
    python_version: str = "3.14"


def _run_install_script(
    tmp_path: Path,
    options: _InstallScriptOptions,
) -> subprocess.CompletedProcess[str]:
    """Execute the install fragment against deterministic command stubs."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not found on PATH")

    stubs_dir = tmp_path / "stubs"
    stubs_dir.mkdir()
    calls_path = tmp_path / "calls"
    github_path = tmp_path / "github-path"
    uv_bin_dir = tmp_path / "uv-bin"
    if options.include_cargo:
        binstall_status = 0 if options.binstall_available else 1
        _write_executable(
            stubs_dir / "cargo",
            f"""#!/bin/bash
set -euo pipefail
if [ "${{1:-}}" = "binstall" ] && [ "${{2:-}}" = "--version" ]; then
  exit {binstall_status}
fi
printf 'cargo' >> "$CALLS_PATH"
printf ' <%s>' "$@" >> "$CALLS_PATH"
printf '\n' >> "$CALLS_PATH"
""",
        )
    if options.include_uv:
        _write_executable(
            stubs_dir / "uv",
            """#!/bin/bash
set -euo pipefail
if [ "${1:-}" = "tool" ] && [ "${2:-}" = "dir" ] && [ "${3:-}" = "--bin" ]; then
  printf '%s\n' "$UV_BIN_DIR"
  exit 0
fi
printf 'uv' >> "$CALLS_PATH"
printf ' <%s>' "$@" >> "$CALLS_PATH"
printf '\n' >> "$CALLS_PATH"
""",
        )

    env = {
        **os.environ,
        "CALLS_PATH": calls_path.as_posix(),
        "GITHUB_PATH": github_path.as_posix(),
        "MERMAN_VERSION": options.merman_version,
        "NIXIE_VERSION": options.nixie_version,
        "PATH": stubs_dir.as_posix(),
        "PYTHON_VERSION": options.python_version,
        "UV_BIN_DIR": str(uv_bin_dir),
    }
    return subprocess.run(  # noqa: S603,TID251 - exercise the bash fragment.
        [bash, "-c", _install_script()],
        check=False,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_manifest_exposes_pinned_version_inputs() -> None:
    """The action should expose the reviewed Nixie toolchain pins."""
    manifest = _load_action()

    assert manifest["runs"]["using"] == "composite"
    assert manifest["inputs"]["nixie-version"]["default"] == "1.1.0"
    assert manifest["inputs"]["merman-version"]["default"] == "0.7.0"
    assert manifest["inputs"]["python-version"]["default"] == "3.14"
    assert manifest["runs"]["steps"][0]["env"] == {
        "NIXIE_VERSION": "${{ inputs.nixie-version }}",
        "MERMAN_VERSION": "${{ inputs.merman-version }}",
        "PYTHON_VERSION": "${{ inputs.python-version }}",
    }
    assert "outputs" not in manifest


def test_install_script_prefers_cargo_binstall(tmp_path: Path) -> None:
    """Merman should use a locked binary install when cargo-binstall exists."""
    result = _run_install_script(
        tmp_path,
        _InstallScriptOptions(binstall_available=True),
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "calls").read_text(encoding="utf-8").splitlines() == [
        "cargo <binstall> <--no-confirm> <--locked> <merman-cli@0.7.0>",
        "uv <tool> <install> <--python> <3.14> <nixie-cli==1.1.0>",
    ]
    assert (tmp_path / "github-path").read_text(encoding="utf-8") == (
        f"{tmp_path / 'uv-bin'}\n"
    )


def test_install_script_falls_back_to_cargo_install(tmp_path: Path) -> None:
    """Merman should use a locked source build without cargo-binstall."""
    result = _run_install_script(
        tmp_path,
        _InstallScriptOptions(binstall_available=False),
    )

    assert result.returncode == 0, result.stderr
    assert "cargo-binstall unavailable" in result.stdout
    assert (tmp_path / "calls").read_text(encoding="utf-8").splitlines() == [
        "cargo <install> <--locked> <merman-cli> <--version> <=0.7.0>",
        "uv <tool> <install> <--python> <3.14> <nixie-cli==1.1.0>",
    ]


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            VersionOverrideCase(
                binstall_available=True,
                merman_version="0.8.0",
                nixie_version="1.2.0",
                python_version="3.13",
                expected_cargo_call=(
                    "cargo <binstall> <--no-confirm> <--locked> <merman-cli@0.8.0>"
                ),
            ),
            id="binstall",
        ),
        pytest.param(
            VersionOverrideCase(
                binstall_available=False,
                merman_version="0.9.0",
                nixie_version="1.3.0",
                python_version="3.12",
                expected_cargo_call=(
                    "cargo <install> <--locked> <merman-cli> <--version> <=0.9.0>"
                ),
            ),
            id="cargo-install",
        ),
    ],
)
def test_install_script_propagates_version_overrides(
    tmp_path: Path,
    case: VersionOverrideCase,
) -> None:
    """Non-default action inputs should reach both installer commands."""
    result = _run_install_script(
        tmp_path,
        _InstallScriptOptions(
            binstall_available=case.binstall_available,
            merman_version=case.merman_version,
            nixie_version=case.nixie_version,
            python_version=case.python_version,
        ),
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "calls").read_text(encoding="utf-8").splitlines() == [
        case.expected_cargo_call,
        f"uv <tool> <install> <--python> <{case.python_version}> "
        f"<nixie-cli=={case.nixie_version}>",
    ]


@pytest.mark.parametrize(
    ("include_cargo", "include_uv", "expected_error"),
    [
        (False, True, "cargo is required to install merman-cli"),
        (True, False, "uv is required to install nixie-cli"),
    ],
    ids=["missing-cargo", "missing-uv"],
)
def test_install_script_reports_missing_prerequisite(
    tmp_path: Path,
    *,
    include_cargo: bool,
    include_uv: bool,
    expected_error: str,
) -> None:
    """Missing runner prerequisites should produce actionable errors."""
    result = _run_install_script(
        tmp_path,
        _InstallScriptOptions(
            binstall_available=False,
            include_cargo=include_cargo,
            include_uv=include_uv,
        ),
    )

    assert result.returncode == 1
    assert expected_error in result.stderr
