"""Cases for reading local actions from the filesystem.

`read_actions` is the one reader here that touches the disk, so these cases
build real trees under a temporary directory rather than parsed texts.
"""

from __future__ import annotations

import os
import typing as typ

import pytest
from cv005_contracts.actions import read_actions
from cv005_contracts.loading import WorkflowReadingError

if typ.TYPE_CHECKING:
    from pathlib import Path

#: A composite action with one step.
ACTION: typ.Final[str] = "runs:\n  using: composite\n  steps:\n    - run: echo\n"


def _write(root: Path, relative: str, text: str) -> None:
    """Write one file under a tree, creating its directories."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_actions_are_read_at_any_depth_by_their_uses_path(tmp_path: Path) -> None:
    """Both metadata spellings are read, keyed by the path a step names."""
    _write(tmp_path, ".github/actions/build/action.yml", ACTION)
    _write(tmp_path, ".github/actions/nested/deep/action.yaml", ACTION)
    _write(tmp_path, ".github/actions/nested/README.md", "not an action\n")
    found = read_actions(tmp_path)
    assert sorted(found) == [
        ".github/actions/build",
        ".github/actions/nested/deep",
    ], sorted(found)
    assert found[".github/actions/build"]["jobs"] == {
        "action": {"steps": [{"run": "echo"}]}
    }


def test_a_tree_without_actions_reads_as_none(tmp_path: Path) -> None:
    """A repository need hold no local actions."""
    assert read_actions(tmp_path) == {}


def test_a_malformed_action_is_named_by_its_path(tmp_path: Path) -> None:
    """Every action's file is `action.yml`, so the failure names its path."""
    _write(tmp_path, ".github/actions/broken/action.yml", "runs: [\n")
    with pytest.raises(WorkflowReadingError, match=r"\.github/actions/broken"):
        read_actions(tmp_path)


@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0,
    reason="needs POSIX permissions and a user that is not root",
)
def test_a_directory_that_cannot_be_listed_is_refused(tmp_path: Path) -> None:
    """A skipped directory would drop its actions from the closure in silence."""
    hidden = tmp_path / ".github" / "actions" / "hidden"
    _write(tmp_path, ".github/actions/hidden/action.yml", ACTION)
    hidden.chmod(0)
    try:
        with pytest.raises(WorkflowReadingError, match="could not be listed"):
            read_actions(tmp_path)
    finally:
        hidden.chmod(0o700)


@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0,
    reason="needs POSIX permissions and a user that is not root",
)
def test_an_actions_directory_that_cannot_be_checked_is_refused(
    tmp_path: Path,
) -> None:
    """Only absence reads as no actions; any other failure is reported."""
    github = tmp_path / ".github"
    _write(tmp_path, ".github/actions/build/action.yml", ACTION)
    github.chmod(0o600)
    try:
        with pytest.raises(WorkflowReadingError, match="could not be checked"):
            read_actions(tmp_path)
    finally:
        github.chmod(0o700)
