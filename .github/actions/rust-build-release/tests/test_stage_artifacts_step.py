"""Tests covering the Stage artefacts step mapping."""

from __future__ import annotations

import os
import typing as typ
from pathlib import Path

import yaml
from plumbum import local

from test_support.plumbum_helpers import run_plumbum_command

if typ.TYPE_CHECKING:
    from cmd_utils import RunResult
else:
    RunResult = object

TARGET = "aarch64-unknown-linux-gnu"
BIN_NAME = "weaver"


def _load_stage_step() -> dict[str, object]:
    action = Path(__file__).resolve().parents[1] / "action.yml"
    data = yaml.safe_load(action.read_text(encoding="utf-8"))
    steps: list[dict[str, object]] = data["runs"]["steps"]
    for step in steps:
        if step.get("id") == "stage-artefacts":
            return step
    message = "stage-artefacts step missing from action"
    raise AssertionError(message)


def _stage_script() -> Path:
    return Path(__file__).resolve().parents[1] / "src" / "stage_artefacts.sh"


def _stage_script_text() -> str:
    return _stage_script().read_text(encoding="utf-8")


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_binary(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"binary")


def _run_stage_script(tmp_path: Path, github_output: Path | None = None) -> RunResult:
    command = local["bash"][str(_stage_script()), TARGET, BIN_NAME]
    env = {"GITHUB_OUTPUT": str(github_output)} if github_output else None
    return run_plumbum_command(command, method="run", cwd=tmp_path, env=env)


def test_stage_step_condition_includes_illumos() -> None:
    """Validate illumos targets trigger the Stage artefacts step."""
    step = _load_stage_step()
    condition = step.get("if")
    assert isinstance(condition, str)
    assert "unknown-linux-" in condition
    assert "unknown-illumos" in condition


def test_stage_step_maps_illumos_to_expected_tuple() -> None:
    """Ensure the illumos target maps to illumos/amd64 output tuple."""
    run_script = _stage_script_text()
    assert "x86_64-unknown-illumos" in run_script
    lines = run_script.split("\n")
    in_illumos_case = False
    found_os = False
    found_arch = False
    for line in lines:
        if "x86_64-unknown-illumos" in line:
            in_illumos_case = True
            continue
        if in_illumos_case and "os=illumos" in line:
            found_os = True
        if in_illumos_case and "arch=amd64" in line:
            found_arch = True
        if in_illumos_case and line.strip() == ";;":
            break
    assert found_os, "os=illumos not found in illumos case block"
    assert found_arch, "arch=amd64 not found in illumos case block"


def test_stage_step_preserves_existing_linux_mapping() -> None:
    """Verify existing Linux target mappings remain correct."""
    run_script = _stage_script_text()
    assert "x86_64-unknown-linux-" in run_script
    assert "os=linux" in run_script
    lines = run_script.split("\n")
    in_x86_linux_case = False
    found_os = False
    found_arch = False
    for line in lines:
        if "x86_64-unknown-linux-" in line:
            in_x86_linux_case = True
            continue
        if in_x86_linux_case and "os=linux" in line:
            found_os = True
        if in_x86_linux_case and "arch=amd64" in line:
            found_arch = True
        if in_x86_linux_case and line.strip() == ";;":
            message = "expected linux/amd64 mapping in x86_64 case"
            if not (found_os and found_arch):
                raise AssertionError(message)
            break


def test_stage_step_errors_for_unsupported_target() -> None:
    """Confirm unsupported targets trigger the default error path."""
    run_script = _stage_script_text()
    assert "::error:: unsupported target" in run_script
    lines = run_script.split("\n")
    default_case_lines: list[str] = []
    in_default_case = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("*)"):
            in_default_case = True
            continue
        if in_default_case and stripped == ";;":
            break
        if in_default_case:
            default_case_lines.append(stripped)
    assert default_case_lines, "default case not found in stage script"
    assert any("::error:: unsupported target" in line for line in default_case_lines)
    assert any(line == "exit 1" for line in default_case_lines)


def test_stage_step_delegates_to_stage_script() -> None:
    """Confirm the composite action calls the staged artefact helper."""
    step = _load_stage_step()
    run_script = step.get("run")
    assert isinstance(run_script, str)
    assert "$GITHUB_ACTION_PATH/src/stage_artefacts.sh" in run_script
    assert "${{ inputs.target }}" in run_script
    assert "${{ inputs.bin-name }}" in run_script


def test_stage_script_prefers_generated_manpage(tmp_path: Path) -> None:
    """Prefer deterministic generated-man output over stale Cargo OUT_DIR pages."""
    _write_binary(tmp_path / f"target/{TARGET}/release/{BIN_NAME}")
    _write_file(
        tmp_path / f"target/generated-man/{TARGET}/release/{BIN_NAME}.1",
        "stable manpage\n",
    )
    _write_file(
        tmp_path / f"target/{TARGET}/release/build/weaver-cli-old/out/{BIN_NAME}.1",
        "old cached manpage\n",
    )
    _write_file(
        tmp_path / f"target/{TARGET}/release/build/weaver-cli-new/out/{BIN_NAME}.1",
        "new cached manpage\n",
    )
    output_file = tmp_path / "github-output"

    result = _run_stage_script(tmp_path, output_file)

    assert result.returncode == 0
    staged = tmp_path / f"dist/{BIN_NAME}_linux_arm64/{BIN_NAME}.1"
    assert staged.read_text(encoding="utf-8") == "stable manpage\n"
    assert output_file.read_text(encoding="utf-8") == (
        f"man-path=dist/{BIN_NAME}_linux_arm64/{BIN_NAME}.1\n"
    )


def test_stage_script_uses_newest_out_dir_manpage_when_needed(tmp_path: Path) -> None:
    """Use the newest Cargo OUT_DIR manpage when no generated-man page exists."""
    _write_binary(tmp_path / f"target/{TARGET}/release/{BIN_NAME}")
    old = tmp_path / f"target/{TARGET}/release/build/weaver-cli-old/out/{BIN_NAME}.1"
    new = tmp_path / f"target/{TARGET}/release/build/weaver-cli-new/out/{BIN_NAME}.1"
    _write_file(old, "old cached manpage\n")
    _write_file(new, "new cached manpage\n")
    os.utime(old, (1, 1))
    os.utime(new, (2, 2))

    result = _run_stage_script(tmp_path)

    assert result.returncode == 0
    assert "found 2 build-script man pages" in result.stdout
    staged = tmp_path / f"dist/{BIN_NAME}_linux_arm64/{BIN_NAME}.1"
    assert staged.read_text(encoding="utf-8") == "new cached manpage\n"
