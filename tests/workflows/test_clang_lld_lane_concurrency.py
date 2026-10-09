"""The setup-rust clang and lld lane cancels runs a newer push supersedes.

The lane installs tools and builds a toy application on three operating
systems, so a superseded run holds Linux, macOS and Windows runners for no
benefit. The group is keyed on the workflow and the ref, which cancels an
older run of this lane on the same branch and touches nothing else.
"""

from __future__ import annotations

import typing as typ

from . import _workflow_reading as reading

WORKFLOW: typ.Final[str] = "test-setup-rust-clang-lld.yml"
EXPECTED_GROUP: typ.Final[str] = "${{ github.workflow }}-${{ github.ref }}"


def _concurrency() -> object:
    """Return the lane's top-level ``concurrency`` mapping, if any."""
    document = reading.load_workflow(WORKFLOW)
    return document.get("concurrency")


def test_the_lane_groups_runs_by_workflow_and_ref() -> None:
    """The group is exactly the workflow and ref, so only this lane's runs pair up."""
    concurrency = _concurrency()

    assert isinstance(concurrency, dict), f"no concurrency mapping: {concurrency!r}"
    assert concurrency.get("group") == EXPECTED_GROUP


def test_the_lane_cancels_superseded_runs() -> None:
    """``cancel-in-progress`` is the boolean ``true``, not the string."""
    concurrency = _concurrency()

    assert isinstance(concurrency, dict), f"no concurrency mapping: {concurrency!r}"
    assert concurrency.get("cancel-in-progress") is True
