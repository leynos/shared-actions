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


def _workflow() -> dict[str, typ.Any]:
    """Parse the reusable workflow."""
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _setup_rust_steps() -> list[dict[str, typ.Any]]:
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
    triggers = _workflow().get("on", _workflow().get(True))
    declared = triggers["workflow_call"]["inputs"].get("install-clang-lld")

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
        forwarded = (step.get("with") or {}).get("install-clang-lld")
        assert forwarded == FORWARD, (
            "setup-rust must receive install-clang-lld from the workflow input; "
            f"got {forwarded!r}"
        )
