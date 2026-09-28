"""The cv005-contracts library lane runs for the library and nothing else.

The library under `packages/cv005-contracts` has its own workflow so that a
library change does not start the action self-test lanes. That only holds
while the workflow's `paths:` filter names the package and itself exactly:
a filter missing the package leaves library changes untested, and one wider
than the package puts the lane back on every pull request.
"""

from __future__ import annotations

import fnmatch
import typing as typ
from pathlib import Path

import pytest

from .workflow_triggers import triggers
from .workflow_yaml import load_workflow

WORKFLOW_PATH: typ.Final[Path] = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "workflows"
    / "test-cv005-contracts.yml"
)
EXPECTED_PATHS: typ.Final[list[str]] = [
    "packages/cv005-contracts/**",
    ".github/workflows/test-cv005-contracts.yml",
]


def _pull_request_paths(document: object) -> object:
    """Return the `paths` filter of a workflow's `pull_request` trigger."""
    configuration = triggers(typ.cast("dict[typ.Any, typ.Any]", document)).get(
        "pull_request"
    )
    return configuration.get("paths") if isinstance(configuration, dict) else None


def _runs_for(paths: object, changed: str) -> bool:
    """Return whether a `paths` filter admits one changed file."""
    patterns = paths if isinstance(paths, list) else []
    return any(fnmatch.fnmatch(changed, str(pattern)) for pattern in patterns)


def test_the_lane_filters_to_the_library_exactly() -> None:
    """The filter names the package and the workflow, and nothing else."""
    paths = _pull_request_paths(load_workflow(WORKFLOW_PATH))
    assert paths == EXPECTED_PATHS, paths


@pytest.mark.parametrize(
    ("changed", "expected"),
    [
        ("packages/cv005-contracts/cv005_contracts/reach.py", True),
        (".github/workflows/test-cv005-contracts.yml", True),
        (".github/actions/generate-coverage/action.yml", False),
        ("cmd_utils.py", False),
        ("packages/other/module.py", False),
    ],
)
def test_the_lane_runs_for_library_changes_alone(
    changed: str, *, expected: bool
) -> None:
    """A library change starts the lane; an action change does not."""
    paths = _pull_request_paths(load_workflow(WORKFLOW_PATH))
    assert _runs_for(paths, changed) is expected, (changed, paths)
