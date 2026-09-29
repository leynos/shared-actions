"""The configured interpreter must sit inside the project's `requires-python`.

`uv sync` refuses an interpreter the project's own declaration excludes, and
the coverage step then fails; create-labels #114 and docx-comment-extractor #44
hit exactly that against `>=3.14`. Each case writes a `pyproject.toml` beside
the compliant tree and asserts on the clause, with a narrow case that passes.
"""

from __future__ import annotations

import typing as typ

import pytest
from contract_fixtures import tree
from cv005_contracts import violations
from matrix_fixtures import write_repository

if typ.TYPE_CHECKING:
    from pathlib import Path

CONFIG: typ.Final[str] = 'repository = "leynos/example"\ninterpreter = "3.13"\n'


def _clauses(root: Path, pyproject: str | None, config: str = CONFIG) -> list[str]:
    """Return the clause of every finding for the tree with a `pyproject.toml`."""
    write_repository(root, tree(), config)
    if pyproject is not None:
        (root / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    return [item.clause for item in violations(root)]


def _project(requirement: str) -> str:
    """Return a `pyproject.toml` declaring one `requires-python`."""
    return f'[project]\nname = "x"\nrequires-python = "{requirement}"\n'


@pytest.mark.parametrize(
    "requirement", [">=3.14", "<3.13", "==3.12.*", ">=3.13.1,<3.13.1"]
)
def test_an_interpreter_outside_the_range_is_refused(
    tmp_path: Path, requirement: str
) -> None:
    """A lower bound above it, an upper bound at it, or another minor is refused."""
    assert _clauses(tmp_path, _project(requirement)) == ["coverage.requires-python"]


@pytest.mark.parametrize(
    "requirement",
    [
        ">=3.12",
        ">=3.13",
        "<3.14",
        ">=3.12,<3.14",
        "~=3.13.0",
        "==3.13.*",
        ">=3.13.1",
        "<3.13.2",
        ">3.13,<3.14",
    ],
)
def test_an_interpreter_inside_the_range_is_accepted(
    tmp_path: Path, requirement: str
) -> None:
    """The narrow direction: any range some 3.13 patch satisfies passes."""
    assert _clauses(tmp_path, _project(requirement)) == []


@pytest.mark.parametrize(
    ("interpreter", "requirement", "expected"),
    [
        ("3.13.5", ">=3.13.5", []),
        ("3.13.5", ">=3.13.6", ["coverage.requires-python"]),
        ("3.13.5", "<3.13.5", ["coverage.requires-python"]),
    ],
)
def test_a_patch_interpreter_is_compared_exactly(
    tmp_path: Path, interpreter: str, requirement: str, expected: list[str]
) -> None:
    """An `X.Y.Z` interpreter is one version, not a family."""
    config = f'repository = "leynos/example"\ninterpreter = "{interpreter}"\n'
    found = [
        c
        for c in _clauses(tmp_path, _project(requirement), config)
        if c.endswith("requires-python")
    ]
    assert found == expected


def test_no_pyproject_is_not_a_violation(tmp_path: Path) -> None:
    """A repository with no `pyproject.toml` declares nothing to violate."""
    assert _clauses(tmp_path, None) == []


@pytest.mark.parametrize("text", ["", "[tool.x]\na = 1\n", '[project]\nname = "x"\n'])
def test_no_requires_python_is_not_a_violation(tmp_path: Path, text: str) -> None:
    """An empty file, no `[project]`, or no `requires-python` declares no range."""
    assert _clauses(tmp_path, text) == []


@pytest.mark.parametrize(
    "text",
    [
        "[project\n",
        "[project]\nrequires-python = 3.13\n",
        '[project]\nrequires-python = "not a range"\n',
    ],
)
def test_an_unreadable_or_invalid_declaration_is_refused(
    tmp_path: Path, text: str
) -> None:
    """Failing closed: a declaration that cannot be judged is a finding."""
    assert _clauses(tmp_path, text) == ["coverage.requires-python"]


def test_a_repository_configuring_no_interpreter_is_not_judged(tmp_path: Path) -> None:
    """The clause runs only where `interpreter` is set, like `coverage.interpreter`."""
    found = _clauses(tmp_path, _project(">=3.14"), 'repository = "leynos/example"\n')
    assert "coverage.requires-python" not in found, found
