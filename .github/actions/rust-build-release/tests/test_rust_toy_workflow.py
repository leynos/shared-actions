"""Tests for the rust-toy-app end-to-end workflow manifest."""

from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOW_PATH = Path(__file__).resolve().parents[3] / "workflows" / "rust-toy-app.yml"


def _load_workflow() -> dict[str, object]:
    """Load the rust-toy-app workflow manifest."""
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _verify_artefacts_script() -> str:
    """Return the shell script from the Verify artefacts workflow step."""
    workflow = _load_workflow()
    jobs = workflow["jobs"]
    build_release = jobs["build-release"]
    steps = build_release["steps"]
    verify_step = next(step for step in steps if step.get("id") == "verify-artefacts")
    return verify_step["run"]


def test_rust_build_release_call_keeps_default_manpage_discovery() -> None:
    """The clap_mangen toy workflow must not opt out of man-page discovery."""
    workflow = _load_workflow()
    jobs = workflow["jobs"]
    build_release = jobs["build-release"]
    steps = build_release["steps"]
    build_steps = [
        step
        for step in steps
        if step.get("uses") == "./.github/actions/rust-build-release"
    ]

    assert build_steps
    for step in build_steps:
        with_inputs = step.get("with", {})
        assert "skip-man-page-discovery" not in with_inputs


def test_verify_artefacts_uses_stable_manpage_path() -> None:
    """The workflow should verify the generated-man path emitted by build.rs."""
    script = _verify_artefacts_script()

    assert (
        'manpage_path="target/generated-man/${{ matrix.target }}/release/'
        'rust-toy-app.1"'
    ) in script
    assert 'test -f "$manpage_path"' in script
    assert 'echo "manpage=rust-toy-app/$manpage_path" >> "$GITHUB_OUTPUT"' in script
    assert 'echo "manpage-relative=${manpage_path}" >> "$GITHUB_OUTPUT"' in script
    assert "release/build/rust-toy-app-*/out/rust-toy-app.1" not in script
