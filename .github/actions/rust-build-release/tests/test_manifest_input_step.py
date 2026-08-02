"""Tests for input wiring declared in the composite action manifest.

These assert the manifest's declared shape only. The RUSTFLAGS export step's
runtime behaviour lives in ``test_rustflags_export.py``, which executes its
shell fragment.
"""

from __future__ import annotations

from rust_build_release_test_helpers import find_step, load_action_manifest


def test_manifest_path_input_declared() -> None:
    """The manifest-path input must exist with a Cargo.toml default."""
    manifest = load_action_manifest()
    inputs = manifest["inputs"]
    assert "manifest-path" in inputs
    manifest_input = inputs["manifest-path"]
    assert manifest_input.get("required", False) is False
    assert manifest_input.get("default") == "Cargo.toml"


def test_toolchain_input_declared() -> None:
    """The toolchain override input must exist with an empty default."""
    manifest = load_action_manifest()
    inputs = manifest["inputs"]
    assert "toolchain" in inputs
    toolchain_input = inputs["toolchain"]
    assert toolchain_input.get("required", False) is False
    assert toolchain_input.get("default") == ""


def test_skip_man_page_discovery_input_declared() -> None:
    """The opt-out input must preserve discovery by default."""
    manifest = load_action_manifest()
    inputs = manifest["inputs"]
    assert "skip-man-page-discovery" in inputs
    skip_input = inputs["skip-man-page-discovery"]
    assert skip_input.get("required", False) is False
    assert skip_input.get("default") == "false"
    assert "post-build step" in skip_input.get("description", "")


def test_build_step_exports_manifest_path_env() -> None:
    """Build step should pass manifest-path via RBR_MANIFEST_PATH."""
    manifest = load_action_manifest()
    steps: list[dict[str, object]] = manifest["runs"]["steps"]
    build_step = find_step(steps, "Build release")
    env = build_step.get("env")
    assert isinstance(env, dict)
    assert env.get("RBR_MANIFEST_PATH") == "${{ inputs.manifest-path }}"


def test_determine_toolchain_step_uses_project_lookup_inputs() -> None:
    """Toolchain lookup must run in project-dir and receive both override inputs."""
    manifest = load_action_manifest()
    steps: list[dict[str, object]] = manifest["runs"]["steps"]
    determine_step = find_step(steps, "Determine toolchain")
    assert determine_step.get("working-directory") == "${{ inputs.project-dir }}"
    run_script = determine_step.get("run")
    assert isinstance(run_script, str)
    assert '--toolchain "${{ inputs.toolchain }}"' in run_script
    assert '--manifest-path "${{ inputs.manifest-path }}"' in run_script


def test_stage_artefacts_step_uses_stable_manpage_path() -> None:
    """Packaging should prefer generated-man before falling back to Cargo output."""
    manifest = load_action_manifest()
    steps: list[dict[str, object]] = manifest["runs"]["steps"]
    stage_step = find_step(steps, "Stage artefacts")
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
    manifest = load_action_manifest()
    inputs = manifest["inputs"]
    assert "rustflags" in inputs, f"rustflags input missing; declared: {sorted(inputs)}"
    rustflags_input = inputs["rustflags"]
    assert rustflags_input.get("required", False) is False, (
        "rustflags must stay optional so existing callers need no change"
    )
    assert rustflags_input.get("default") == "", (
        "the default must be empty so the environment is left untouched; "
        f"got {rustflags_input.get('default')!r}"
    )


def test_export_rustflags_step_wiring() -> None:
    """The export step must gate on the input and defer to an inherited value."""
    manifest = load_action_manifest()
    steps: list[dict[str, object]] = manifest["runs"]["steps"]
    export_step = find_step(steps, "Export caller RUSTFLAGS")
    assert export_step.get("if") == "inputs.rustflags != ''", (
        "the step must be skipped entirely when no rustflags input is given; "
        f"got {export_step.get('if')!r}"
    )
    env = export_step.get("env")
    assert isinstance(env, dict), "export step declares no env block"
    assert env.get("RBR_RUSTFLAGS") == "${{ inputs.rustflags }}", (
        f"rustflags must reach the script via RBR_RUSTFLAGS; got {env!r}"
    )
    run_script = export_step.get("run")
    assert isinstance(run_script, str), "export step has no run script"
    # The value must flow through the environment, not template expansion,
    # and an inherited RUSTFLAGS must win over the input.
    assert "if [[ ${RUSTFLAGS+x} ]]; then" in run_script, (
        "the inherited-value guard must use the bash 3.2 compatible "
        "${RUSTFLAGS+x} form rather than [[ -v ]], which macOS bash cannot parse"
    )
    assert '"$RBR_RUSTFLAGS"' in run_script, (
        "the script must read the value from the environment variable"
    )
    assert "${{" not in run_script, (
        "the caller's value must not be interpolated into the script by the "
        "expression template engine"
    )


def test_export_rustflags_step_precedes_toolchain_setup() -> None:
    """The export must run before the nested setup-rust toolchain step."""
    manifest = load_action_manifest()
    steps: list[dict[str, object]] = manifest["runs"]["steps"]
    names = [step.get("name") for step in steps]
    assert names.index("Export caller RUSTFLAGS") < names.index(
        "Setup Rust toolchain"
    ), (
        "the export must precede toolchain setup, whose nested step only "
        f"defers to an already-set RUSTFLAGS; step order was {names}"
    )
