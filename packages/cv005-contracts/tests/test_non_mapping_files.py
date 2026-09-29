"""A workflow or action file that is not a mapping is a reading failure.

nile-valley #116 found a reader that treated a file parsing to a list, a
scalar or nothing as an empty workflow, so the contract had no subject and
passed. Every non-mapping text is refused here with the file named, from
the loader up to the command line's exit status.
"""

from __future__ import annotations

import typing as typ

import pytest
from contract_fixtures import tree
from cv005_contracts.actions import read_actions
from cv005_contracts.cli import EXIT_CLEAN, EXIT_UNREADABLE, check
from cv005_contracts.loading import (
    WorkflowReadingError,
    load_workflow,
    read_workflows,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

#: Texts whose top level is not a mapping, each a shape a lax reader treats
#: as empty: nothing, a comment, an explicit null, a falsy scalar, a list,
#: and a scalar that is truthy.
NON_MAPPINGS: typ.Final[list[str]] = [
    "",
    "# only a comment\n",
    "null\n",
    "~\n",
    "0\n",
    "[]\n",
    "- on: push\n",
    "just text\n",
    "42\n",
]


@pytest.mark.parametrize("text", NON_MAPPINGS)
def test_a_non_mapping_workflow_is_refused(text: str) -> None:
    """The loader refuses a text that does not parse to a mapping."""
    with pytest.raises(WorkflowReadingError, match="top-level mapping"):
        load_workflow(text)


def test_more_than_one_document_is_refused() -> None:
    """A second document would be a second, unread workflow."""
    with pytest.raises(WorkflowReadingError, match="not a workflow document"):
        load_workflow("on: push\njobs: {}\n---\non: push\njobs: {}\n")


@pytest.mark.parametrize("text", NON_MAPPINGS)
def test_a_non_mapping_file_among_good_ones_names_itself(
    tmp_path: Path, text: str
) -> None:
    """One such file fails the whole read, naming it; the rest do not excuse it."""
    (tmp_path / "ok.yml").write_text("on: push\njobs: {}\n", encoding="utf-8")
    (tmp_path / "junk.yml").write_text(text, encoding="utf-8")
    with pytest.raises(WorkflowReadingError, match=r"junk\.yml"):
        read_workflows(tmp_path)


@pytest.mark.parametrize("text", NON_MAPPINGS)
def test_a_non_mapping_action_is_named_by_its_path(tmp_path: Path, text: str) -> None:
    """A local action file is held to the same rule, named by its `uses:` path."""
    path = tmp_path / ".github" / "actions" / "junk" / "action.yml"
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")
    with pytest.raises(WorkflowReadingError, match=r"\.github/actions/junk"):
        read_actions(tmp_path)


def _write_repository(root: Path, extra: str | None) -> Path:
    """Write the compliant tree, and one more workflow when `extra` is given."""
    workflows = root / ".github" / "workflows"
    workflows.mkdir(parents=True)
    for name, text in tree().items():
        (workflows / name).write_text(text, encoding="utf-8")
    if extra is not None:
        (workflows / "extra.yml").write_text(extra, encoding="utf-8")
    (root / ".github" / "cv005.toml").write_text(
        'repository = "leynos/example"\ninterpreter = "3.13"\n', encoding="utf-8"
    )
    return root


@pytest.mark.parametrize("text", NON_MAPPINGS)
def test_the_command_exits_unreadable_rather_than_passing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], text: str
) -> None:
    """A non-mapping file is exit 2 naming the file, never exit 0."""
    root = _write_repository(tmp_path, text)
    assert check(repository=root) == EXIT_UNREADABLE
    assert "extra.yml" in capsys.readouterr().err


def test_the_same_tree_without_the_extra_file_is_clean(tmp_path: Path) -> None:
    """The refusals above are the extra file's doing, not the fixture's."""
    assert check(repository=_write_repository(tmp_path, None)) == EXIT_CLEAN
