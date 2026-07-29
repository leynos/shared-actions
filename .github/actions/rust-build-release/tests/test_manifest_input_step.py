"""Tests for manifest-path input wiring in the composite action."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ACTION_PATH = Path(__file__).resolve().parents[1] / "action.yml"
# A value crafted to close a fixed heredoc delimiter early and have the
# remainder read back as further environment-file commands.
INJECTION_MARKER = "__RBR_RUSTFLAGS_EOF__"
INJECTED_RUSTFLAGS = f"-Zpolonius=next\n{INJECTION_MARKER}\nRBR_INJECTED=1"


def _load_action_manifest() -> dict[str, object]:
    return yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))


def _find_step(steps: list[dict[str, object]], name: str) -> dict[str, object]:
    for step in steps:
        if step.get("name") == name:
            return step
    message = f"step '{name}' missing from action"
    raise AssertionError(message)


def _export_rustflags_run_script() -> str:
    """Return the shell fragment that exports the caller's RUSTFLAGS."""
    steps: list[dict[str, object]] = _load_action_manifest()["runs"]["steps"]
    run_script = _find_step(steps, "Export caller RUSTFLAGS").get("run")
    assert isinstance(run_script, str), "export step has no run script"
    return run_script


def _run_export_script(
    tmp_path: Path, rustflags: str, *, inherited: str | None = None
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run the export fragment and return its result with the env-file text."""
    bash = shutil.which("bash")
    if bash is None:  # pragma: no cover - bash is present on supported runners
        pytest.skip("bash not found on PATH")
    tmp_path.mkdir(parents=True, exist_ok=True)
    github_env = tmp_path / "github-env"
    github_env.touch()
    env = {key: value for key, value in os.environ.items() if key != "RUSTFLAGS"}
    env["GITHUB_ENV"] = github_env.as_posix()
    env["RBR_RUSTFLAGS"] = rustflags
    if inherited is not None:
        env["RUSTFLAGS"] = inherited
    result = subprocess.run(  # noqa: S603,TID251 - exercise the bash fragment.
        [bash, "-c", _export_rustflags_run_script()],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result, github_env.read_text(encoding="utf-8")


def _parse_env_file(text: str) -> dict[str, str]:
    """Parse ``GITHUB_ENV`` content, honouring heredoc-delimited values."""
    values: dict[str, str] = {}
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if not line:
            continue
        name, separator, remainder = line.partition("=")
        if separator:
            values[name] = remainder
            continue
        name, separator, delimiter = line.partition("<<")
        if not separator:
            message = f"unparsable environment-file line: {line!r}"
            raise AssertionError(message)
        collected: list[str] = []
        while index < len(lines) and lines[index] != delimiter:
            collected.append(lines[index])
            index += 1
        if index >= len(lines):
            message = f"unterminated heredoc for {name}"
            raise AssertionError(message)
        index += 1
        values[name] = "\n".join(collected)
    return values


def test_manifest_path_input_declared() -> None:
    """The manifest-path input must exist with a Cargo.toml default."""
    manifest = _load_action_manifest()
    inputs = manifest["inputs"]
    assert "manifest-path" in inputs
    manifest_input = inputs["manifest-path"]
    assert manifest_input.get("required", False) is False
    assert manifest_input.get("default") == "Cargo.toml"


def test_toolchain_input_declared() -> None:
    """The toolchain override input must exist with an empty default."""
    manifest = _load_action_manifest()
    inputs = manifest["inputs"]
    assert "toolchain" in inputs
    toolchain_input = inputs["toolchain"]
    assert toolchain_input.get("required", False) is False
    assert toolchain_input.get("default") == ""


def test_skip_man_page_discovery_input_declared() -> None:
    """The opt-out input must preserve discovery by default."""
    manifest = _load_action_manifest()
    inputs = manifest["inputs"]
    assert "skip-man-page-discovery" in inputs
    skip_input = inputs["skip-man-page-discovery"]
    assert skip_input.get("required", False) is False
    assert skip_input.get("default") == "false"
    assert "post-build step" in skip_input.get("description", "")


def test_build_step_exports_manifest_path_env() -> None:
    """Build step should pass manifest-path via RBR_MANIFEST_PATH."""
    manifest = _load_action_manifest()
    steps: list[dict[str, object]] = manifest["runs"]["steps"]
    build_step = _find_step(steps, "Build release")
    env = build_step.get("env")
    assert isinstance(env, dict)
    assert env.get("RBR_MANIFEST_PATH") == "${{ inputs.manifest-path }}"


def test_determine_toolchain_step_uses_project_lookup_inputs() -> None:
    """Toolchain lookup must run in project-dir and receive both override inputs."""
    manifest = _load_action_manifest()
    steps: list[dict[str, object]] = manifest["runs"]["steps"]
    determine_step = _find_step(steps, "Determine toolchain")
    assert determine_step.get("working-directory") == "${{ inputs.project-dir }}"
    run_script = determine_step.get("run")
    assert isinstance(run_script, str)
    assert '--toolchain "${{ inputs.toolchain }}"' in run_script
    assert '--manifest-path "${{ inputs.manifest-path }}"' in run_script


def test_stage_artefacts_step_uses_stable_manpage_path() -> None:
    """Packaging should prefer generated-man before falling back to Cargo output."""
    manifest = _load_action_manifest()
    steps: list[dict[str, object]] = manifest["runs"]["steps"]
    stage_step = _find_step(steps, "Stage artefacts")
    run_script = stage_step.get("run")
    assert isinstance(run_script, str)
    assert (
        'stable_man_path="target/generated-man/${{ inputs.target }}/release/'
        '${{ inputs.bin-name }}.1"'
    ) in run_script
    assert (
        'if [[ "${{ inputs.skip-man-page-discovery }}" != "true" ]]; then' in run_script
    )
    assert 'if [[ ! -f "${man_path}" ]]; then' in run_script
    assert "release/build" in run_script
    assert "man_matches" in run_script


def test_rustflags_input_declared() -> None:
    """The rustflags input must exist with an empty default."""
    manifest = _load_action_manifest()
    inputs = manifest["inputs"]
    assert "rustflags" in inputs
    rustflags_input = inputs["rustflags"]
    assert rustflags_input.get("required", False) is False
    assert rustflags_input.get("default") == ""


def test_export_rustflags_step_wiring() -> None:
    """The export step must gate on the input and defer to an inherited value."""
    manifest = _load_action_manifest()
    steps: list[dict[str, object]] = manifest["runs"]["steps"]
    export_step = _find_step(steps, "Export caller RUSTFLAGS")
    assert export_step.get("if") == "inputs.rustflags != ''"
    env = export_step.get("env")
    assert isinstance(env, dict)
    assert env.get("RBR_RUSTFLAGS") == "${{ inputs.rustflags }}"
    run_script = export_step.get("run")
    assert isinstance(run_script, str)
    # The value must flow through the environment, not template expansion,
    # and an inherited RUSTFLAGS must win over the input.
    assert "-v RUSTFLAGS" in run_script
    assert '"$RBR_RUSTFLAGS"' in run_script
    assert "${{" not in run_script


def test_export_rustflags_step_uses_a_generated_delimiter() -> None:
    """The heredoc delimiter must not be a fixed literal in the manifest."""
    run_script = _export_rustflags_run_script()
    assert f"RUSTFLAGS<<{INJECTION_MARKER}" not in run_script
    assert 'echo "RUSTFLAGS<<$delimiter"' in run_script


def test_export_rustflags_writes_single_line_value(tmp_path: Path) -> None:
    """An ordinary value round-trips through the environment file."""
    result, env_text = _run_export_script(tmp_path, "-Zpolonius=next")

    assert result.returncode == 0, result.stderr
    assert _parse_env_file(env_text) == {"RUSTFLAGS": "-Zpolonius=next"}


def test_export_rustflags_contains_delimiter_lookalike(tmp_path: Path) -> None:
    """A value carrying the old fixed marker must not escape its heredoc."""
    result, env_text = _run_export_script(tmp_path, INJECTED_RUSTFLAGS)

    assert result.returncode == 0, result.stderr
    parsed = _parse_env_file(env_text)
    # The marker stays inside RUSTFLAGS rather than closing it, so nothing
    # after it is read back as a separate environment-file assignment.
    assert parsed == {"RUSTFLAGS": INJECTED_RUSTFLAGS}
    assert "RBR_INJECTED" not in parsed


def test_export_rustflags_delimiter_differs_between_runs(tmp_path: Path) -> None:
    """Delimiters are generated per run so callers cannot predict them."""
    _, first = _run_export_script(tmp_path / "first", "-Zpolonius=next")
    _, second = _run_export_script(tmp_path / "second", "-Zpolonius=next")

    assert first.splitlines()[0] != second.splitlines()[0]


def test_export_rustflags_defers_to_inherited_value(tmp_path: Path) -> None:
    """An inherited RUSTFLAGS wins and nothing is written to the env file."""
    result, env_text = _run_export_script(
        tmp_path, "-Zpolonius=next", inherited="-D warnings"
    )

    assert result.returncode == 0, result.stderr
    assert env_text == ""
    assert "leaving the inherited value in place" in result.stderr


def test_export_rustflags_step_precedes_toolchain_setup() -> None:
    """The export must run before the nested setup-rust toolchain step."""
    manifest = _load_action_manifest()
    steps: list[dict[str, object]] = manifest["runs"]["steps"]
    names = [step.get("name") for step in steps]
    assert names.index("Export caller RUSTFLAGS") < names.index("Setup Rust toolchain")
