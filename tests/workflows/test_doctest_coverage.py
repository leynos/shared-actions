"""Contract that every docstring example this repository holds is executed.

Before issue #484, nothing collected them. `pytest.ini` names its
testpaths explicitly and the `test` target passed no doctest flag, so 63
example lines across nine files were inert: they read as documentation
and were checked against nothing. Nine of them had never been true, two
referencing names the example never imported and one carrying a
continuation marker left inside its expected output by a formatter.

The `doctest` target fixes that, but it names its modules in a list, and
a list is the same trap testpaths was: a module added with examples runs
nowhere and passes by never running. It has to be a list, because
`--doctest-modules` imports everything it collects and many action
scripts are importable only with the `sys.path` their own action sets up.

So this module holds the list to the tree. It finds every file carrying a
`>>>` and fails, naming the file, when one is not covered. It asserts the
list is non-empty too, since a target that collects nothing passes.
"""

from __future__ import annotations

import re
import typing as typ
from pathlib import Path

import pytest

REPOSITORY_ROOT: typ.Final[Path] = Path(__file__).resolve().parents[2]
MAKEFILE: typ.Final[Path] = REPOSITORY_ROOT / "Makefile"

#: The Makefile variable naming what the `doctest` target collects.
DOCTEST_PATHS_VARIABLE: typ.Final[str] = "DOCTEST_PATHS"

#: Directories that hold no source of this repository's own, so a `>>>`
#: inside one is somebody else's example and not ours to execute.
EXCLUDED_DIRECTORIES: typ.Final[frozenset[str]] = frozenset(
    {
        ".git",
        ".venv",
        ".uv-cache",
        ".uv-tools",
        "__pycache__",
        "node_modules",
        "target",
        "dist",
    }
)

#: A prompt at the start of a line, which is what makes a docstring example
#: an example. Matched with leading whitespace consumed, because an example
#: is indented inside the docstring that carries it.
_PROMPT: typ.Final[re.Pattern[str]] = re.compile(r"^[ \t]*>>> ", re.MULTILINE)


def _makefile_variable(name: str) -> list[str]:
    """Return the whitespace-separated words of one Makefile variable.

    Line continuations are joined first, so a value spread over several
    lines reads as the single list the recipe receives.
    """
    text = MAKEFILE.read_text(encoding="utf-8").replace("\\\n", " ")
    for line in text.splitlines():
        stripped = line.strip()
        for operator in ("?=", ":=", "="):
            prefix = f"{name} {operator}"
            if stripped.startswith(prefix):
                return stripped[len(prefix) :].split()
    msg = f"{MAKEFILE} declares no {name}"
    raise AssertionError(msg)


def _python_files_with_examples() -> list[Path]:
    """Return every Python file of this repository holding an example."""
    found: list[Path] = []
    for path in REPOSITORY_ROOT.rglob("*.py"):
        if EXCLUDED_DIRECTORIES.intersection(path.relative_to(REPOSITORY_ROOT).parts):
            continue
        if _PROMPT.search(path.read_text(encoding="utf-8", errors="ignore")):
            found.append(path.relative_to(REPOSITORY_ROOT))
    return sorted(found)


def _is_covered(candidate: Path, collected: list[str]) -> bool:
    """Return True when the `doctest` target would collect *candidate*.

    An entry names the file itself or a directory above it, which is how
    pytest reads its own arguments.
    """
    return any(
        candidate == Path(entry) or Path(entry) in candidate.parents
        for entry in collected
    )


@pytest.fixture(scope="module")
def collected_paths() -> list[str]:
    """Return what the `doctest` target passes to pytest."""
    return _makefile_variable(DOCTEST_PATHS_VARIABLE)


class TestDoctestCoverage:
    """Whether every example in the tree is one the gate executes."""

    def test_the_target_collects_something(self, collected_paths: list[str]) -> None:
        """The doctest target names at least one path.

        A target that collects nothing exits zero and proves nothing, so
        an empty list would satisfy every other rule here.
        """
        assert collected_paths, (
            f"{MAKEFILE} sets {DOCTEST_PATHS_VARIABLE} to nothing, so the "
            "doctest target would collect no examples and pass"
        )

    def test_the_tree_still_has_examples(self) -> None:
        """Some file in the tree carries an example.

        Without this, deleting every example in the repository would
        satisfy the coverage rule below by leaving it nothing to check.
        """
        assert _python_files_with_examples(), (
            "no file in the repository carries a docstring example; the "
            "coverage rule below now asserts nothing"
        )

    def test_every_file_with_an_example_is_collected(
        self, collected_paths: list[str]
    ) -> None:
        """Every example in the tree is one the gate runs.

        This is the rule. A module added with examples and not named in
        the target would otherwise read as checked documentation while
        being checked by nothing, which is the state this whole change
        exists to leave behind.
        """
        uncovered = [
            str(path)
            for path in _python_files_with_examples()
            if not _is_covered(path, collected_paths)
        ]
        assert not uncovered, (
            f"these files carry docstring examples that nothing executes: "
            f"{uncovered}; add each to {DOCTEST_PATHS_VARIABLE} in {MAKEFILE}"
        )

    def test_every_collected_path_exists(self, collected_paths: list[str]) -> None:
        """A path named in the target is a path that is there.

        A renamed module leaves an entry behind that silently collects
        nothing, and pytest exits four rather than failing a test, so the
        gate would go red with no assertion to explain it.
        """
        missing = [
            entry for entry in collected_paths if not (REPOSITORY_ROOT / entry).exists()
        ]
        assert not missing, (
            f"{DOCTEST_PATHS_VARIABLE} names paths that do not exist: {missing}"
        )

    @pytest.mark.parametrize(
        ("candidate", "collected", "expected"),
        [
            pytest.param("a.py", ["a.py"], True, id="named-exactly"),
            pytest.param("pkg/a.py", ["pkg"], True, id="under-a-named-directory"),
            pytest.param("pkg/sub/a.py", ["pkg"], True, id="nested-deeper"),
            pytest.param("b.py", ["a.py"], False, id="a-different-file"),
            pytest.param("other/a.py", ["pkg"], False, id="a-different-directory"),
            pytest.param("pkgx/a.py", ["pkg"], False, id="a-similar-prefix"),
        ],
    )
    def test_coverage_is_by_path_not_by_prefix(
        self,
        candidate: str,
        collected: list[str],
        expected: bool,  # noqa: FBT001 - boolean literals clarify parametrized cases.
    ) -> None:
        """A directory covers what is under it, and nothing merely alike.

        The similar-prefix case is the one worth naming: a rule written
        with string matching would read `pkgx/a.py` as covered by `pkg`
        and let a whole package's examples go unrun.
        """
        assert _is_covered(Path(candidate), collected) is expected
