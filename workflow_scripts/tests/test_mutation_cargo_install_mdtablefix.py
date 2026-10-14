"""Contract for ``mutation-cargo.yml``'s mdtablefix installer input.

The mutation baseline can launch mdtablefix from the caller's tests, so that
binary must be available before caller setup commands and cargo-mutants. This
contract keeps the reusable workflow on the shared prebuilt-binary installer
and makes the install opt-in for existing callers.
"""

from __future__ import annotations

import re
import typing as typ
from pathlib import Path

import pytest
import yaml

WORKFLOW = (
    Path(__file__).resolve().parents[2] / ".github" / "workflows" / "mutation-cargo.yml"
)
INSTALLER_ACTION = "leynos/shared-actions/.github/actions/install-mdtablefix"
INSTALL_STEP = "Install mdtablefix"
RUST_STEP = "Setup Rust"
SETUP_STEP = "Run setup commands"
MUTATION_STEP = "Run mutation testing"

pytestmark = pytest.mark.skipif(
    not WORKFLOW.exists(),
    reason="workflow file not present in this working copy (e.g. inside "
    "mutmut's mutants/ sandbox, which does not copy .github/)",
)


class _Input(typ.TypedDict, total=False):
    """The slice of a workflow input that these tests inspect."""

    type: str
    default: str


# Functional syntax is required because the YAML keys ``if`` and ``with`` are
# Python keywords.
_Step = typ.TypedDict(
    "_Step",
    {"name": str, "if": str, "uses": str, "with": dict[str, str]},
    total=False,
)


class _Job(typ.TypedDict, total=False):
    """The slice of a workflow job that these tests inspect."""

    steps: list[_Step]


class _Workflow(typ.TypedDict):
    """The slice of the reusable workflow that these tests inspect."""

    jobs: dict[str, _Job]


def _document() -> dict[object, object]:
    """Parse the workflow while keeping the YAML loader boundary explicit."""
    return typ.cast(
        "dict[object, object]",
        yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")),
    )


def _workflow() -> _Workflow:
    """Parse the reusable workflow."""
    return typ.cast("_Workflow", _document())


def _inputs() -> dict[str, _Input]:
    """Return the reusable workflow's declared inputs."""
    document = _document()
    triggers = typ.cast(
        "dict[str, dict[str, dict[str, _Input]]]",
        document.get("on", document.get(True)),
    )
    return triggers["workflow_call"]["inputs"]


def _mutants_steps() -> list[_Step]:
    """Return steps from the job that enumerates and runs Rust mutants."""
    return _workflow()["jobs"]["mutants"]["steps"]


def _named_step(name: str) -> _Step:
    """Find one named step in the mutants job."""
    steps = [step for step in _mutants_steps() if step.get("name") == name]
    assert len(steps) == 1, f"expected one {name!r} step, found {len(steps)}"
    return steps[0]


@pytest.mark.parametrize(
    ("name", "expected_default"),
    [
        ("install-mdtablefix", "false"),
        ("mdtablefix-version", "0.6.1"),
        ("mdtablefix-sha256", ""),
    ],
    ids=["opt-in", "version", "digest"],
)
def test_installer_input_has_safe_pinned_default(
    name: str, expected_default: str
) -> None:
    """Existing callers opt out while the shared version matches CI."""
    input_definition = _inputs()[name]

    assert input_definition.get("type") == "string", f"{name} must be a string"
    assert input_definition.get("default") == expected_default, (
        f"{name} must default to {expected_default!r}"
    )


def test_installer_is_conditional_and_forwards_both_pins() -> None:
    """The shared composite action receives the caller's version and digest."""
    step = _named_step(INSTALL_STEP)

    assert step.get("if") == "${{ inputs.install-mdtablefix == 'true' }}", (
        "install-mdtablefix must skip when its input is false but work in act"
    )
    action, separator, pin = step.get("uses", "").partition("@")
    assert separator, "installer reference must include its immutable pin"
    assert action == INSTALLER_ACTION, "installer must use the shared mdtablefix action"
    assert re.fullmatch(r"[0-9a-f]{40}", pin), (
        "installer must be pinned by a full lowercase Git SHA"
    )
    assert step.get("with") == {
        "version": "${{ inputs.mdtablefix-version }}",
        "sha256": "${{ inputs.mdtablefix-sha256 }}",
    }


def test_installer_runs_before_setup_and_mutant_enumeration() -> None:
    """Provision the formatter before caller setup and cargo-mutants."""
    steps = _mutants_steps()
    names = [step.get("name") for step in steps]

    assert names.index(RUST_STEP) < names.index(INSTALL_STEP)
    assert names.index(INSTALL_STEP) < names.index(SETUP_STEP)
    assert names.index(SETUP_STEP) < names.index(MUTATION_STEP)


def test_installer_is_confined_to_the_mutants_job() -> None:
    """Only shards that run the baseline and mutants install mdtablefix."""
    workflow = _workflow()
    jobs = workflow["jobs"]
    installers = [
        (job_name, step)
        for job_name, job in jobs.items()
        for step in job.get("steps", [])
        if step.get("name") == INSTALL_STEP
    ]

    assert len(installers) == 1
    assert installers[0][0] == "mutants"
