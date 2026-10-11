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


#: Workflows producing the contexts the `main-required-checks` ruleset
#: (18427916) requires. A path filter on any of them would leave its
#: contexts pending on a library-only pull request and block the merge, so
#: they stay unfiltered and a library change runs them.
REQUIRED_WORKFLOWS: typ.Final[frozenset[str]] = frozenset(
    {
        "ci.yml",
        "rust-toy-app.yml",
        "test-determine-release-modes.yml",
        "test-export-cargo-metadata.yml",
        "test-rust-build-release-root-discovery.yml",
        "test-stage-release-artefacts.yml",
        "test-upload-release-assets.yml",
    }
)

#: `test-coverage-watchdog.yml` is not here: its own contract
#: (tests/workflows/test_coverage_watchdog_lane.py) requires it to run on
#: every pull request, so that it can become a required check.
#:
#: The exact set of workflows that skip a library-only change: self-test
#: lanes for actions the library cannot affect, which no required check and
#: no other workflow depends on. Anything else runs for a library change,
#: including the main-branch publisher and the Dependabot automerge path.
LIBRARY_SKIPPING_LANES: typ.Final[frozenset[str]] = frozenset(
    {
        "test-dependabot-automerge.yml",
        "test-export-ubicloud-cache-credentials.yml",
        "test-install-mdtablefix.yml",
        "test-install-whitaker.yml",
        "test-setup-rust-clang-lld.yml",
        "test-setup-rust-mold.yml",
        "test-setup-rust-sccache.yml",
        "test-upload-codescene-coverage.yml",
    }
)

#: Workflows a library change must always run although they look like the
#: self-tests beside them: the live Dependabot automerge path, so the
#: library's own bump pull requests still merge, and the main-branch
#: CodeScene publisher, so every commit on main uploads coverage.
NEVER_SKIPPING: typ.Final[frozenset[str]] = frozenset(
    {
        "dependabot-automerge-caller.yml",
        "coverage-main.yml",
    }
)
LIBRARY_PATH: typ.Final[str] = "packages/cv005-contracts/**"


def _ignored_paths(name: str) -> list[object]:
    """Return every `paths-ignore` entry across a workflow's triggers.

    Push triggers count as well as pull-request ones: a publisher skipping
    library-only pushes to main would leave CodeScene without coverage for
    that commit.
    """
    document = load_workflow(WORKFLOW_PATH.parent / name)
    found: list[object] = []
    for configuration in triggers(
        typ.cast("dict[typ.Any, typ.Any]", document)
    ).values():
        ignored = (
            configuration.get("paths-ignore")
            if isinstance(configuration, dict)
            else None
        )
        found.extend(ignored if isinstance(ignored, list) else [])
    return found


@pytest.mark.parametrize("name", sorted(REQUIRED_WORKFLOWS))
def test_no_required_workflow_skips_library_changes(name: str) -> None:
    """A required workflow skipped by a filter would block the merge."""
    assert LIBRARY_PATH not in _ignored_paths(name), name


def test_exactly_the_self_test_lanes_skip_library_changes() -> None:
    """The skip list is pinned: no workflow joins it or leaves it unnoticed."""
    skipping = {
        path.name
        for path in sorted(WORKFLOW_PATH.parent.iterdir())
        if path.suffix in {".yml", ".yaml"}
        and LIBRARY_PATH in _ignored_paths(path.name)
    }
    assert skipping == LIBRARY_SKIPPING_LANES, sorted(skipping ^ LIBRARY_SKIPPING_LANES)


@pytest.mark.parametrize("name", sorted(NEVER_SKIPPING))
def test_the_live_automerge_and_publisher_never_skip_library_changes(
    name: str,
) -> None:
    """The self-tests may skip; the paths they test must not."""
    assert LIBRARY_PATH not in _ignored_paths(name), name
