"""Contract tests for the nested setup-rust reference in rust-build-release.

The nested step is pinned to a full commit SHA of this repository. These tests
hold that pin, the two cache inputs forwarded to it, and the referenced
revision's own input surface together, so a bump that drops ``cache-provider``
or ``use-sccache`` fails here rather than in a downstream workflow.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from rust_build_release_test_helpers import find_step, load_action_manifest

ACTION_PATH = Path(__file__).resolve().parents[1] / "action.yml"
REPO_ROOT = Path(__file__).resolve().parents[4]
SETUP_RUST_MANIFEST = ".github/actions/setup-rust/action.yml"

#: Revision of this repository that the nested setup-rust step must reference.
#: Keep in sync with the ``uses`` value in ``action.yml``; both change together.
EXPECTED_SETUP_RUST_SHA = "7c9d66030879b504365202df90f439ea419e72bd"

#: Inputs rust-build-release forwards, mapped to the default it declares for
#: each. The referenced setup-rust revision must declare every name.
FORWARDED_CACHE_INPUTS = {"cache-provider": "github", "use-sccache": "true"}

_USES_PATTERN = re.compile(
    r"leynos/shared-actions/\.github/actions/setup-rust@([0-9a-f]{40})"
)


def _load_setup_rust_step() -> dict[str, object]:
    steps: list[dict[str, object]] = load_action_manifest()["runs"]["steps"]
    return find_step(steps, "Setup Rust toolchain")


def _pinned_sha() -> str:
    uses = _load_setup_rust_step().get("uses")
    assert isinstance(uses, str)
    match = _USES_PATTERN.fullmatch(uses)
    assert match is not None, f"Expected SHA-pinned reference, got: {uses}"
    return match.group(1)


def _setup_rust_manifest_at(sha: str) -> dict[str, object]:
    """Return the setup-rust manifest at *sha*, skipping when unavailable."""
    git = shutil.which("git")
    if git is None:  # pragma: no cover - environment guard
        pytest.skip("git not found on PATH")
    completed = subprocess.run(  # noqa: S603,TID251 - read a pinned blob.
        [git, "show", f"{sha}:{SETUP_RUST_MANIFEST}"],
        capture_output=True,
        check=False,
        cwd=REPO_ROOT,
        text=True,
    )
    if completed.returncode != 0:  # pragma: no cover - shallow clone guard
        pytest.skip(f"revision {sha} unavailable in this checkout")
    return yaml.safe_load(completed.stdout)


def test_setup_rust_step_uses_tagged_reference() -> None:
    """The setup-rust action should be pinned to a full commit SHA."""
    sha = _pinned_sha()
    assert len(sha) == 40, f"SHA must be exactly 40 hex characters, got: {sha}"


def test_setup_rust_step_includes_tag_comment() -> None:
    """The manifest should name the release tag the pin is measured against."""
    manifest_text = ACTION_PATH.read_text(encoding="utf-8")
    assert "setup-rust-v1" in manifest_text


def test_setup_rust_pin_matches_expected_revision() -> None:
    """The pin should be the revision these tests were written against."""
    assert _pinned_sha() == EXPECTED_SETUP_RUST_SHA


def test_pinned_revision_declares_forwarded_cache_inputs() -> None:
    """The referenced revision must accept the inputs the step forwards."""
    inputs = _setup_rust_manifest_at(EXPECTED_SETUP_RUST_SHA)["inputs"]
    missing = sorted(set(FORWARDED_CACHE_INPUTS) - set(inputs))
    assert not missing, f"setup-rust revision lacks inputs: {missing}"


def test_working_tree_setup_rust_declares_forwarded_cache_inputs() -> None:
    """The checked-in setup-rust manifest must keep those inputs too."""
    manifest = yaml.safe_load(
        (REPO_ROOT / SETUP_RUST_MANIFEST).read_text(encoding="utf-8")
    )
    missing = sorted(set(FORWARDED_CACHE_INPUTS) - set(manifest["inputs"]))
    assert not missing, f"setup-rust working tree lacks inputs: {missing}"


@pytest.mark.parametrize(("name", "default"), sorted(FORWARDED_CACHE_INPUTS.items()))
def test_cache_inputs_declared_with_documented_defaults(
    name: str, default: str
) -> None:
    """rust-build-release must expose both inputs with the documented defaults."""
    declared = load_action_manifest()["inputs"]
    assert name in declared, f"input '{name}' missing from rust-build-release"
    assert declared[name].get("required", False) is False
    assert declared[name].get("default") == default


@pytest.mark.parametrize("name", sorted(FORWARDED_CACHE_INPUTS))
def test_cache_inputs_forwarded_to_nested_setup_rust(name: str) -> None:
    """Both inputs must reach the nested step verbatim."""
    with_block = _load_setup_rust_step().get("with")
    assert isinstance(with_block, dict)
    assert with_block.get(name) == f"${{{{ inputs.{name} }}}}"


def test_cache_provider_validated_before_toolchain_setup() -> None:
    """An unrecognized provider must fail before the nested step runs."""
    steps: list[dict[str, object]] = load_action_manifest()["runs"]["steps"]
    names = [step.get("name") for step in steps]
    assert names.index("Validate cache provider") < names.index("Setup Rust toolchain")
    validate_step = find_step(steps, "Validate cache provider")
    assert validate_step.get("env", {}).get("RBR_CACHE_PROVIDER") == (
        "${{ inputs.cache-provider }}"
    )
    run_script = validate_step.get("run")
    assert isinstance(run_script, str)
    assert "github|external" in run_script
