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
import contextlib
import doctest
import importlib.util
import re
import subprocess
import sys
import tempfile
import textwrap
import typing as typ
from pathlib import Path

import pytest
import yaml

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import types

REPOSITORY_ROOT: typ.Final[Path] = Path(__file__).resolve().parents[2]
MAKEFILE: typ.Final[Path] = REPOSITORY_ROOT / "Makefile"
CI_WORKFLOW: typ.Final[Path] = REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"

#: The Makefile variable naming what the `doctest` target collects.
DOCTEST_PATHS_VARIABLE: typ.Final[str] = "DOCTEST_PATHS"

#: The target that runs the examples. Asserted as a command rather than as
#: a step name, because a step named "Run docstring examples" that runs
#: something else is not this rule, and a differently named one that runs
#: this target is.
DOCTEST_TARGET: typ.Final[str] = "doctest"

#: A `make` invocation naming a target, anchored so that the word inside a
#: longer one is not matched.
_MAKE_TARGET: typ.Final[re.Pattern[str]] = re.compile(
    r"(?:^[ \t]*|[;&|]\s*)make\b(?P<arguments>[^\n;&|]*)",
    re.MULTILINE,
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


#: Definitions whose docstring the finder reads but whose body it never
#: enters. Anything defined inside one is a local name, bound when the
#: call runs and unreachable from the module afterwards.
_OPAQUE: typ.Final = (ast.FunctionDef, ast.AsyncFunctionDef)

#: Definitions whose docstring the finder reads and whose body it walks,
#: because their contents are attributes it can reach by name.
_NAMED_TRANSPARENT: typ.Final = (ast.ClassDef,)

#: Every definition that binds a name in the scope containing it.
_DEFINITIONS: typ.Final = _OPAQUE + _NAMED_TRANSPARENT


def _constant_truth(test: ast.expr) -> bool | None:
    """Return the fixed truth of *test*, or None when it is not fixed."""
    return bool(test.value) if isinstance(test, ast.Constant) else None


def _live_children(node: ast.AST) -> cabc.Iterator[ast.AST]:
    """Yield the children a run of *node* could still reach.

    Only a literally constant condition is decided here. `if False:` binds
    nothing, ever, so a docstring inside it is as unreachable as one in a
    function body. A condition that is merely likely, such as a version
    comparison, is left alone and both arms are walked: over-counting a
    reachable example is a false alarm somebody can read, and pruning a
    branch that does run would hide an example nothing executes.
    """
    for child in ast.iter_child_nodes(node):
        if (
            isinstance(child, ast.If)
            and (truth := _constant_truth(child.test)) is not None
        ):
            yield from (child.body if truth else child.orelse)
        else:
            yield child


def _bind(node: ast.AST, bound: dict[str, list[str]]) -> None:
    """Record into *bound* what the scope rooted at *node* finally binds."""
    for child in _live_children(node):
        if isinstance(child, _DEFINITIONS):
            bound[child.name] = _definition_docstrings(child)
        elif isinstance(child, ast.If):
            _bind_alternatives(child, bound)
        else:
            _bind(child, bound)


def _bind_alternatives(branch: ast.If, bound: dict[str, list[str]]) -> None:
    """Bind both arms of an undecidable `if` into *bound* as alternatives.

    `_live_children` has already decided every constant condition, so an
    `if` reaching here is one no reader can decide. Each arm binds into
    its own copy of the scope, and a name either arm rebinds keeps what
    both arms leave it: `def f` in each arm contributes both docstrings
    rather than the second overwriting the first. A definition after the
    whole `if` still replaces the merged entry, as it replaces whichever
    arm ran.
    """
    arms: list[dict[str, list[str]]] = []
    for statements in (branch.body, branch.orelse):
        arm = dict(bound)
        _bind(ast.Module(body=statements, type_ignores=[]), arm)
        arms.append(arm)
    first, second = arms
    for name in [*first, *(name for name in second if name not in first)]:
        left, right = first.get(name), second.get(name)
        bound[name] = left if left is right else [*(left or []), *(right or [])]


def _definition_docstrings(node: ast.AST) -> list[str]:
    """Return the docstrings one definition contributes to its scope."""
    found = [text] if (text := ast.get_docstring(node, clean=False)) else []
    if isinstance(node, _NAMED_TRANSPARENT):
        found.extend(_scope_docstrings(node))
    return found


def _scope_docstrings(scope: ast.AST) -> list[str]:
    """Return the docstrings *scope* binds, in the order it binds them.

    Keyed by name, so a definition that a later one of the same name
    replaces contributes nothing: only the last binding is an attribute
    of the finished module or class.
    """
    bound: dict[str, list[str]] = {}
    _bind(scope, bound)
    return [text for entry in bound.values() for text in entry]


def _docstrings(tree: ast.Module) -> list[str]:
    """Return every docstring `doctest.DocTestFinder` can reach in *tree*.

    The finder starts at the module and walks names it can reach by
    attribute: classes, their methods, nested classes, and functions
    bound at module level, including ones defined inside an `if` or a
    `try`. It never enters a function body, so a function or class
    defined inside a function is invisible to it, and a string literal
    after an assignment is an attribute docstring that Sphinx renders
    and Python never binds.

    What it reaches is the module as it ends up, not every definition the
    source contains. Two definitions of one name leave only the second
    bound, and a definition inside `if False:` is never bound at all, so
    neither is collected however plainly it is written.
    """
    found = [text] if (text := ast.get_docstring(tree, clean=False)) else []
    found.extend(_scope_docstrings(tree))
    return found


#: Probe modules for the reachability rule, each holding one example.
#: Their docstrings use single quotes so the sources nest inside this
#: module's literals, and the prompt is a placeholder: a literal one at
#: the start of a line would make this file's fixtures look like examples
#: to the very rule they exercise.
_PROBE_PROMPT: typ.Final[str] = "<prompt>"
_REACHABILITY_CASES: typ.Final[dict[str, tuple[str, int, str]]] = {
    "module": (
        """
        '''Mod.

        <prompt> 1
        1
        '''
        """,
        1,
        "the module docstring",
    ),
    "class": (
        """
        class C:
            '''C.

            <prompt> 1
            1
            '''
        """,
        1,
        "a class docstring",
    ),
    "method": (
        """
        class C:
            def m(self):
                '''M.

                <prompt> 1
                1
                '''
        """,
        1,
        "a method docstring",
    ),
    "function": (
        """
        def f():
            '''F.

            <prompt> 1
            1
            '''
        """,
        1,
        "a module-level function docstring",
    ),
    "conditionally-defined": (
        """
        if True:
            def f():
                '''F.

                <prompt> 1
                1
                '''
        """,
        1,
        "a function bound inside a module-level if",
    ),
    "nested-function": (
        """
        def outer():
            def inner():
                '''Inner.

                <prompt> 1
                1
                '''
        """,
        0,
        "a function defined inside another function",
    ),
    "local-class": (
        """
        def outer():
            class Local:
                '''Local.

                <prompt> 1
                1
                '''
        """,
        0,
        "a class defined inside a function",
    ),
    "shadowed-function": (
        """
        def f():
            '''F, the definition that loses.

            <prompt> 1
            1
            '''


        def f():  # noqa: F811 - two definitions of one name is the case
            '''F, the definition that wins.'''
        """,
        0,
        "a docstring on a definition a later one of the same name replaces",
    ),
    "shadowed-function-both-with-examples": (
        """
        def f():
            '''F, the definition that loses.

            <prompt> 1
            1
            '''


        def f():  # noqa: F811 - two definitions of one name is the case
            '''F, the definition that wins.

            <prompt> 2
            2
            '''
        """,
        1,
        "only the surviving definition of a shadowed name",
    ),
    "never-defined": (
        """
        if False:
            def f():
                '''F.

                <prompt> 1
                1
                '''
        """,
        0,
        "a function inside `if False`, which binds nothing",
    ),
    "the-arm-that-runs": (
        """
        if False:
            def f():
                '''F, unreachable.

                <prompt> 1
                1
                '''
        else:
            def f():
                '''F, reachable.

                <prompt> 2
                2
                '''
        """,
        1,
        "only the arm a constant condition leaves reachable",
    ),
    "condition-that-is-not-constant": (
        """
        if len('') == 0:
            def f():
                '''F.

                <prompt> 1
                1
                '''
        """,
        1,
        "a function under a condition no reader can decide",
    ),
    "replaced-after-an-undecidable-condition": (
        """
        if len('') == 0:
            def f():
                '''F.

                <prompt> 1
                1
                '''
        else:
            def f():
                '''F.

                <prompt> 1
                1
                '''
        def f():
            '''F.

            <prompt> 1
            1
            '''
        """,
        1,
        "only the definition after the if, which replaces either arm",
    ),
    "attribute-docstring": (
        """
        X = 1
        '''X.

        <prompt> 1
        1
        '''
        """,
        0,
        "an attribute docstring after an assignment",
    ),
}


#: The name the probe module is imported under. It is removed from
#: `sys.modules` again, so nothing else can import it by accident.
_PROBE_MODULE: typ.Final[str] = "reachability_probe"


@contextlib.contextmanager
def _probe_module(source: str) -> cabc.Iterator[types.ModuleType]:
    """Import *source* as a throwaway module, and unregister it afterwards.

    The finder needs a real module object rather than a source string, so
    the probe is written to a file and imported through `importlib`
    instead of being handed to `exec`. The registration is undone in a
    `finally`, so a probe that raises on import leaves nothing behind in
    `sys.modules` for the next case to pick up.
    """
    with tempfile.TemporaryDirectory() as raw:
        path = Path(raw) / f"{_PROBE_MODULE}.py"
        path.write_text(source, encoding="utf-8")
        spec = importlib.util.spec_from_file_location(_PROBE_MODULE, path)
        if spec is None or spec.loader is None:
            msg = f"no import spec for the probe module at {path}"
            raise ImportError(msg)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
            yield module
        finally:
            sys.modules.pop(spec.name, None)


def _finder_prompt_count(source: str) -> int:
    """Return how many examples `doctest.DocTestFinder` finds in *source*.

    The oracle for the reachability rule. It imports the module, which is
    what the finder needs, so it is used only on the short sources
    written in this file.
    """
    with _probe_module(source) as module:
        return sum(
            len(test.examples)
            for test in doctest.DocTestFinder().find(module, name=_PROBE_MODULE)
        )


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

    def test_the_pull_request_lane_runs_the_examples(self) -> None:
        """A pull request runs them, not only a developer's machine.

        `make test` depends on the doctest target, and no job here runs
        `make test`: the macOS and Windows legs run `uv run pytest`, and
        the Linux suite runs under the coverage action, which builds its
        own invocation. Wiring the target to `test` alone would leave a
        wrong example passing every job on a pull request while failing
        locally, which is the wrong way round and is what review found.
        """
        document = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
        runners = [
            step.get("name", "<unnamed>")
            for job in (document.get("jobs") or {}).values()
            for step in (job.get("steps") or [])
            for match in _MAKE_TARGET.finditer(str(step.get("run", "")))
            if DOCTEST_TARGET
            in [
                token
                for token in match.group("arguments").split()
                if not token.startswith("-") and "=" not in token
            ]
        ]
        assert runners, (
            f"no step in ci.yml runs `make {DOCTEST_TARGET}`, so a wrong "
            "docstring example would pass every job on a pull request"
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

    def test_both_arms_of_an_undecidable_condition_contribute(self) -> None:
        """An `if` no reader can decide contributes from both arms.

        This one cannot be checked against `DocTestFinder`, and that is
        the point. The finder imports the module, so the interpreter
        decides the condition and only one arm ever binds; a static
        reader cannot know which. Over-counting is the safe direction: a
        false positive is an example somebody can go and look at, while
        pruning the arm that does run would hide an example the gate
        never executes, which is the hole this module exists to close.

        Without this case the fixture set cannot tell the reader apart
        from one that defaults an undecidable condition to "take the
        body", because every oracle case has a condition the interpreter
        decides the same way a defaulting reader would guess.
        """
        source = textwrap.dedent(
            """
            if len('') == 0:
                def f():
                    '''F, the arm that happens to run.

                    <prompt> 1
                    1
                    '''
            else:
                def g():
                    '''G, the arm that happens not to.

                    <prompt> 2
                    2
                    '''
            """
        ).replace(_PROBE_PROMPT, ">" * 3)
        reachable = sum(
            len(_PROMPT.findall(docstring))
            for docstring in _docstrings(ast.parse(source))
        )

        assert reachable == 2, (
            "both arms of an undecidable condition must contribute; counting "
            f"{reachable} means one arm was pruned on a guess, and the arm "
            "pruned may be the one that runs"
        )

    def test_one_name_in_both_undecidable_arms_contributes_twice(self) -> None:
        """`def f` in each arm of an undecidable `if` keeps both docstrings.

        Like the case above, this cannot be checked against the finder,
        which sees only the arm that ran. Keyed by name alone, the second
        arm would overwrite the first, and the gate would then demand an
        example it has already found be listed again.
        """
        source = textwrap.dedent(
            """
            if len('') == 0:
                def f():
                    '''F, one arm.

                    <prompt> 1
                    1
                    '''
            else:
                def f():
                    '''F, the other.

                    <prompt> 2
                    2
                    '''
            """
        ).replace(_PROBE_PROMPT, ">" * 3)
        reachable = sum(
            len(_PROMPT.findall(docstring))
            for docstring in _docstrings(ast.parse(source))
        )

        assert reachable == 2, (
            "a name bound in both arms of an undecidable condition must keep "
            f"both alternatives; counting {reachable} means one arm overwrote "
            "the other"
        )

    @pytest.mark.parametrize(
        ("source", "expected", "reason"),
        [
            pytest.param(source, expected, reason, id=name)
            for name, (source, expected, reason) in _REACHABILITY_CASES.items()
        ],
    )
    def test_reachability_matches_the_finder(
        self,
        source: str,
        expected: int,
        reason: str,
    ) -> None:
        """Reachability is what `DocTestFinder` walks, not what `ast` can see.

        The finder starts at the module and follows attributes, so it
        reads a class, its methods and a function bound at module level
        even inside an `if`, and it never enters a function body. A
        traversal that simply walked every node would count a nested
        function's examples as reachable while the gate could not run
        them, which is the same "listed but never executed" hole this
        module exists to close. Each case is checked against the
        finder's own verdict, so the rule cannot drift from it.
        """
        module_source = textwrap.dedent(source).replace(_PROBE_PROMPT, ">" * 3)
        reachable = sum(
            len(_PROMPT.findall(docstring))
            for docstring in _docstrings(ast.parse(module_source))
        )

        assert reachable == expected, reason
        assert reachable == _finder_prompt_count(module_source), reason

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
