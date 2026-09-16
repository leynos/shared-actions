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

import ast
import re
import subprocess
import typing as typ
from pathlib import Path

import pytest

if typ.TYPE_CHECKING:
    import collections.abc as cabc

REPOSITORY_ROOT: typ.Final[Path] = Path(__file__).resolve().parents[2]
MAKEFILE: typ.Final[Path] = REPOSITORY_ROOT / "Makefile"

#: The Makefile variable naming what the `doctest` target collects.
DOCTEST_PATHS_VARIABLE: typ.Final[str] = "DOCTEST_PATHS"

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


def _tracked_python_files() -> list[Path]:
    """Return every tracked Python file, relative to the repository root.

    Tracked rather than everything on disk, and that is the whole point.
    Walking the tree meant deciding which directories were somebody else's,
    and no deny-list stays complete: the coverage action builds a
    throwaway environment at `.venv-coverage` in the working directory,
    full of third-party examples, and a list naming `.venv` did not cover
    it. What git tracks is exactly this repository's own source.
    """
    completed = subprocess.run(  # noqa: TID251 - a fixed, argument-free command.
        ["git", "ls-files", "-z", "--", "*.py"],  # noqa: S607 - git is on PATH.
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return [Path(entry) for entry in completed.stdout.split("\0") if entry]


def _files_with_examples(candidates: cabc.Iterable[Path]) -> list[Path]:
    """Return those *candidates* whose text holds a docstring example."""
    return sorted(
        path
        for path in candidates
        if _PROMPT.search(
            (REPOSITORY_ROOT / path).read_text(encoding="utf-8", errors="ignore")
        )
    )


def _python_files_with_examples() -> list[Path]:
    """Return every tracked Python file of this repository holding an example."""
    return _files_with_examples(_tracked_python_files())


def _docstrings(tree: ast.Module) -> list[str]:
    """Return every docstring `doctest.DocTestFinder` can reach in *tree*.

    Module, class and function docstrings, which is exactly the set the
    finder walks. A string literal sitting after an assignment is an
    attribute docstring: Sphinx renders it, Python does not bind it, and
    the finder never sees it.
    """
    carriers = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    return [
        text
        for node in ast.walk(tree)
        if isinstance(node, carriers) and (text := ast.get_docstring(node, clean=False))
    ]


def _prompt_counts(path: Path) -> tuple[int, int]:
    """Return how many prompts *path* holds, and how many are reachable.

    The first number counts every prompt in the file. The second counts
    only those inside a docstring the finder walks. They differ exactly
    when an example is written somewhere nothing will run it.
    """
    text = (REPOSITORY_ROOT / path).read_text(encoding="utf-8", errors="ignore")
    total = len(_PROMPT.findall(text))
    reachable = sum(
        len(_PROMPT.findall(docstring)) for docstring in _docstrings(ast.parse(text))
    )
    return total, reachable


def _is_covered(candidate: Path, collected: list[str]) -> bool:
    """Return True when the `doctest` target would collect *candidate*.

    An entry names the file itself or a directory above it, which is how
    pytest reads its own arguments.
    """
    return any(
        candidate == Path(entry) or Path(entry) in candidate.parents
        for entry in collected
    )


@pytest.fixture
def untracked_example_file() -> cabc.Iterator[Path]:
    """Create an untracked module with an example, and remove it after.

    Written inside the repository on purpose: the point is what the
    listing does with a file git does not track, and a file outside the
    repository would never be a candidate in the first place.
    """
    directory = REPOSITORY_ROOT / ".venv-coverage" / "lib"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "third_party_example.py"
    path.write_text('def f():\n    """Doc.\n\n    >>> f()\n    1\n    """\n')
    try:
        yield path.relative_to(REPOSITORY_ROOT)
    finally:
        path.unlink(missing_ok=True)
        for parent in (directory, directory.parent):
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()


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

    def test_an_untracked_file_is_not_this_repository_s(
        self, untracked_example_file: Path
    ) -> None:
        """A file git does not track is not ours to execute.

        This is the regression. The listing used to walk the tree behind a
        deny-list of directory names, and the coverage action builds a
        throwaway environment at `.venv-coverage` in the working
        directory, full of third-party examples. The deny-list named
        `.venv` and did not cover it, so the rule failed in CI on
        somebody else's docstring.
        """
        assert (
            untracked_example_file.exists()
            or (REPOSITORY_ROOT / untracked_example_file).exists()
        )
        assert untracked_example_file not in _python_files_with_examples()

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

    def test_every_prompt_sits_where_doctest_can_reach_it(self) -> None:
        """An example outside a docstring is not collected, only listed.

        Naming a file proves the file has a prompt; it does not prove
        pytest will run it. `test_support/ansi.py` carried two examples
        in an attribute docstring, a string after an assignment, which
        Sphinx renders and `DocTestFinder` never walks. The path rule
        called that file covered and both examples were inert, which is
        the very defect this whole change exists to end, hiding inside
        the change meant to end it.
        """
        unreachable = {
            str(path): (total, reachable)
            for path in _python_files_with_examples()
            if (counts := _prompt_counts(path))[0] != counts[1]
            for total, reachable in (counts,)
        }
        assert not unreachable, (
            "these files hold prompts that doctest cannot reach, shown as "
            f"(prompts, reachable): {unreachable}; move each example into a "
            "module, class or function docstring"
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
