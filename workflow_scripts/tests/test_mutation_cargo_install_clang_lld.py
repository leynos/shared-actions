"""Contract for ``mutation-cargo.yml``'s ``install-clang-lld`` passthrough.

Callers whose ``.cargo/config.toml`` selects clang or lld need them on the
mutation runner, and the reusable workflow calls ``setup-rust`` from its own
checkout, so a caller cannot reach that step's inputs directly. The input is
therefore declared on the workflow and forwarded to the step. Each half is
asserted separately, because an input that is declared but never forwarded
looks configurable while every caller gets the default, and a forward of a
literal or of another input silently ignores the caller's choice.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest
import yaml

WORKFLOW = (
    Path(__file__).resolve().parents[2] / ".github" / "workflows" / "mutation-cargo.yml"
)
SETUP_RUST_USE = "./workflow-src/.github/actions/setup-rust"
FORWARD = "${{ inputs.install-clang-lld }}"

pytestmark = pytest.mark.skipif(
    not WORKFLOW.exists(),
    reason="workflow file not present in this working copy (e.g. inside "
    "mutmut's mutants/ sandbox, which does not copy .github/)",
)


class _Input(typ.TypedDict, total=False):
    """The slice of a ``workflow_call`` input that these tests inspect."""

    type: str
    default: str


#: The slice of a workflow step that these tests inspect. The functional form
#: is needed because ``with`` is a keyword and cannot be a class attribute.
_Step = typ.TypedDict("_Step", {"uses": str, "with": dict[str, str]}, total=False)


class _Job(typ.TypedDict, total=False):
    """The slice of a workflow job that these tests inspect."""

    steps: list[_Step]


class _Workflow(typ.TypedDict):
    """The slice of the reusable workflow that these tests inspect."""

    jobs: dict[str, _Job]


def _document() -> dict[object, object]:
    """Parse the reusable workflow into its raw mapping."""
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _workflow() -> _Workflow:
    """Return the parsed reusable workflow."""
    return typ.cast("_Workflow", _document())


def _declared_inputs() -> dict[str, _Input]:
    """Return the ``workflow_call`` inputs, whichever way YAML read ``on``.

    YAML 1.1 reads the bare key ``on`` as the boolean ``True``.
    """
    document = _document()
    triggers = typ.cast(
        "dict[str, dict[str, dict[str, _Input]]]",
        document.get("on", document.get(True)),
    )
    return triggers["workflow_call"]["inputs"]


def _setup_rust_steps() -> list[_Step]:
    """Return every step that runs the local ``setup-rust`` action."""
    return [
        step
        for job in _workflow()["jobs"].values()
        for step in job.get("steps", [])
        if step.get("uses") == SETUP_RUST_USE
    ]


def test_the_workflow_declares_install_clang_lld_defaulting_to_false() -> None:
    """A caller can ask for clang and lld, and one that does not gets no install.

    The default is the string ``'false'``, not a boolean, because the
    ``setup-rust`` input it feeds accepts only the strings ``true`` and
    ``false``.
    """
    declared = _declared_inputs().get("install-clang-lld")

    assert declared is not None, "mutation-cargo.yml must expose install-clang-lld"
    assert declared.get("type") == "string"
    assert declared.get("default") == "false", (
        "install-clang-lld must default to the string 'false', or every existing "
        f"caller starts installing clang and lld; got {declared.get('default')!r}"
    )


def test_setup_rust_receives_the_callers_install_clang_lld() -> None:
    """The step forwards the caller's input, not a literal or another input."""
    steps = _setup_rust_steps()

    assert steps, "mutation-cargo.yml must run the local setup-rust action"
    for step in steps:
        forwarded = step.get("with", {}).get("install-clang-lld")
        assert forwarded == FORWARD, (
            "setup-rust must receive install-clang-lld from the workflow input; "
            f"got {forwarded!r}"
        )
