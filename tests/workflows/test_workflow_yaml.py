"""The workflow parser refuses what GitHub refuses and PyYAML forgives.

Driven on files written for the purpose: this repository's workflows declare
no key twice, so a reader that kept the last value would pass over them.

Run via ``make test``.
"""

from __future__ import annotations

import typing as typ

import pytest

from .test_coverage_timeout_tiers import workflow_documents
from .workflow_yaml import load_workflow

if typ.TYPE_CHECKING:
    from pathlib import Path

#: A lane whose first ``runs-on`` bills and whose second reads as hosted.
#: PyYAML keeps the second, so a placement contract over the parse would
#: judge a runner GitHub never schedules.
TWICE_DECLARED_RUNNER: typ.Final[str] = (
    "on:\n"
    "  pull_request:\n"
    "jobs:\n"
    "  coverage:\n"
    "    runs-on: ubicloud-standard-8\n"
    "    runs-on: ubuntu-latest\n"
    "    steps:\n"
    "      - run: 'true'\n"
)


def test_a_key_declared_twice_is_refused(tmp_path: Path) -> None:
    """The duplicate is an error naming the file, not a silent last-wins."""
    path = tmp_path / "ci.yml"
    path.write_text(TWICE_DECLARED_RUNNER, encoding="utf-8")

    with pytest.raises(ValueError, match=r"(?s)ci\.yml .*duplicate key 'runs-on'"):
        load_workflow(path)


def test_the_repository_reader_refuses_it_too(tmp_path: Path) -> None:
    """Every contract parses through ``workflow_documents``, so it must refuse.

    A strict loader that nothing called would leave the contracts reading
    the forgiving parse.
    """
    (tmp_path / "ci.yml").write_text(TWICE_DECLARED_RUNNER, encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate key"):
        workflow_documents(tmp_path)


def test_invalid_yaml_names_the_file(tmp_path: Path) -> None:
    """The parser's own error names a stream, not the workflow at fault."""
    path = tmp_path / "broken.yaml"
    path.write_text("jobs: [unclosed\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"broken\.yaml"):
        load_workflow(path)


def test_the_same_key_in_sibling_mappings_is_accepted(tmp_path: Path) -> None:
    """Uniqueness is per mapping; two jobs each declare their own runner."""
    path = tmp_path / "ci.yml"
    path.write_text(
        "jobs:\n  a:\n    runs-on: ubuntu-latest\n  b:\n    runs-on: windows-latest\n",
        encoding="utf-8",
    )

    assert load_workflow(path) == {
        "jobs": {
            "a": {"runs-on": "ubuntu-latest"},
            "b": {"runs-on": "windows-latest"},
        }
    }


def test_an_unhashable_key_is_left_to_the_parser(tmp_path: Path) -> None:
    """A sequence used as a key is the base loader's error, named the same way.

    The duplicate check cannot hash it, so it defers rather than raising a
    ``TypeError`` that no caller would read as a malformed workflow.
    """
    path = tmp_path / "ci.yml"
    path.write_text("? [a, b]\n: c\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"(?s)ci\.yml .*unhashable key"):
        load_workflow(path)
