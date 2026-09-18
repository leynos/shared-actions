"""Contract that the Python suite executes once per platform on a pull request.

`ci.yml` used to run the suite twice on Linux for every pull request:
once uninstrumented in the `python-tests` matrix leg, and again under
the coverage action in the `coverage` job. The coverage job's own
comment says it installs nfpm and the Rust toolchain precisely so that
the same tests execute there rather than skip, so the two runs covered
the same ground. The uninstrumented one cost 5 minutes 14 seconds on
the run measured and reported nothing the instrumented one did not.

The rule is the estate's: the coverage job is the only Linux test
execution. This module holds `ci.yml` to it from both sides. One test
says the matrix leg's suite step cannot reach Linux; the other says the
coverage job really does run a suite, so that satisfying the first by
deleting the second is not an option.

The steps are matched by what they invoke, not by their names. A step
named "Run tests" that no longer runs any is not what this rule is
about, and a differently named step that runs the suite is. That
includes reaching it through a make target: `make test` and `make all`
both run the whole suite here, so a step calling either repeats the run
just as plainly as one calling `pytest`.
"""

from __future__ import annotations

import re
import typing as typ
from pathlib import Path

import pytest
import yaml

if typ.TYPE_CHECKING:
    import collections.abc as cabc

REPOSITORY_ROOT: typ.Final[Path] = Path(__file__).resolve().parents[2]
CI_WORKFLOW: typ.Final[Path] = REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"

#: The condition that keeps a step off every Linux runner in this
#: repository's matrix, whose only other arm is macOS.
NON_LINUX_CONDITION: typ.Final[str] = "runner.os == 'macOS'"

#: The action the coverage job delegates the instrumented run to.
COVERAGE_ACTION: typ.Final[str] = "./.github/actions/generate-coverage"

#: A `pytest` invocation as a command, anchored to a line start or a
#: shell separator so that the word inside a path such as
#: `.pytest_cache` or an option such as `--pytest-workers` does not
#: count as running the suite.
#:
#: Leading whitespace is consumed after the line anchor, exactly as
#: `_MAKE_INVOCATION` does below. An indented `pytest` inside a loop or a
#: conditional body runs the suite as plainly as one at column zero, and
#: requiring `pytest` immediately after the line start let every indented
#: one through.
_PYTEST_INVOCATION: typ.Final[re.Pattern[str]] = re.compile(
    r"(?:^[ \t]*|[;&|]\s*|\buv run\s+(?:--[^\s]+\s+)*)pytest(?:\s|$)",
    re.MULTILINE,
)

#: Make targets that run the whole suite. `test` is the suite itself and
#: `all` runs it among the other gates, so a step reaching for either
#: repeats the run whatever it is named. Without this, adding
#: `run: make test` beside the guarded step would put the suite back on
#: Linux while both assertions below still passed.
SUITE_MAKE_TARGETS: typ.Final[frozenset[str]] = frozenset({"test", "all"})

#: Shell keywords a command can sit behind without being any less run.
#: `if make test; then ...; fi` executes the suite exactly as plainly as
#: `make test` does, and matching only after a line start or a separator
#: would let it through.
_SHELL_PREFIX_KEYWORDS: typ.Final[tuple[str, ...]] = (
    "if",
    "then",
    "else",
    "elif",
    "do",
    "while",
    "until",
)

#: A `make` invocation, with its arguments captured. Options and
#: variable assignments are left in the capture and filtered by token,
#: so `make TEST_ARGS=-x lint` is not read as running the suite.
#:
#: The keywords are matched as whole words, so a target named
#: `verify-something` cannot be read as a keyword ending in one, and a
#: `make` inside a longer word such as `remake` is not matched at all.
#:
#: Leading whitespace is consumed after the line anchor. A `make test`
#: indented inside a loop or a conditional body runs the suite exactly
#: as plainly as one at column zero, and requiring `make` immediately
#: after the line start would have let every indented one through.
#:
#: The pattern is assembled from the module-level literal above rather
#: than from anything a workflow supplies, so there is no input here to
#: drive backtracking.
_MAKE_INVOCATION: typ.Final[re.Pattern[str]] = re.compile(
    r"(?:^[ \t]*|[;&|]\s*|(?:!\s*)|\b(?:"
    + "|".join(_SHELL_PREFIX_KEYWORDS)
    + r")\s+)make\b(?:\s+(?P<arguments>[^\n;&|]*))?",
    re.MULTILINE,
)


def _make_runs_the_suite(script: str) -> bool:
    """Return True when *script* invokes a make target that runs the suite."""
    for match in _MAKE_INVOCATION.finditer(script):
        tokens = (match.group("arguments") or "").split()
        targets = [
            token for token in tokens if not token.startswith("-") and "=" not in token
        ]
        if SUITE_MAKE_TARGETS.intersection(targets):
            return True
    return False


@pytest.fixture(scope="module")
def ci_jobs() -> cabc.Mapping[str, dict[str, typ.Any]]:
    """Return the jobs of `ci.yml`."""
    document = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    return document["jobs"]


def _suite_steps(job: cabc.Mapping[str, typ.Any]) -> list[dict[str, typ.Any]]:
    """Return the steps of *job* that run the suite.

    Directly, through `pytest`, or through a make target that wraps it.
    """
    return [
        step
        for step in job.get("steps") or []
        if _PYTEST_INVOCATION.search(script := str(step.get("run", "")))
        or _make_runs_the_suite(script)
    ]


def test_the_matrix_leg_runs_the_suite_somewhere(
    ci_jobs: cabc.Mapping[str, dict[str, typ.Any]],
) -> None:
    """The matrix leg still has a suite step to constrain.

    Without this, deleting the step would satisfy the Linux rule below
    while quietly ending macOS coverage of the suite too.
    """
    steps = _suite_steps(ci_jobs["python-tests"])
    assert steps, (
        "ci.yml::python-tests runs pytest in no step; the macOS leg has "
        "stopped running the suite"
    )


def test_the_matrix_leg_cannot_run_the_suite_on_linux(
    ci_jobs: cabc.Mapping[str, dict[str, typ.Any]],
) -> None:
    """Every suite step in the matrix leg is confined to macOS.

    An unconditional step is the defect this rule exists to prevent: it
    reads as harmless and repeats the whole suite on the leg that has
    already been measured running it.
    """
    offenders = [
        (step.get("name", "<unnamed>"), step.get("if"))
        for step in _suite_steps(ci_jobs["python-tests"])
        if str(step.get("if", "")).strip() != NON_LINUX_CONDITION
    ]
    assert not offenders, (
        "ci.yml::python-tests runs pytest on Linux in "
        f"{offenders}; the coverage job is the only Linux test execution, so "
        f"guard each with `if: {NON_LINUX_CONDITION}`"
    )


def test_the_coverage_job_executes_the_suite(
    ci_jobs: cabc.Mapping[str, dict[str, typ.Any]],
) -> None:
    """The coverage job delegates to the coverage action.

    This is the other half of the rule. The Linux leg gives up its own
    run of the suite on the understanding that this job makes it, so
    the moment this step goes the repository has no Linux test
    execution at all.
    """
    uses = [step.get("uses") for step in ci_jobs["coverage"].get("steps") or []]
    assert COVERAGE_ACTION in uses, (
        f"ci.yml::coverage no longer uses {COVERAGE_ACTION}; nothing runs the "
        "Python suite on Linux"
    )


@pytest.mark.parametrize(
    ("script", "expected"),
    [
        pytest.param("make test", True, id="column-zero"),
        pytest.param("    make test", True, id="indented"),
        pytest.param("\tmake test", True, id="tab-indented"),
        pytest.param(
            "for d in a b; do\n    make test\ndone",
            True,
            id="indented-in-a-loop-body",
        ),
        pytest.param("if true; then\n  make all\nfi", True, id="indented-make-all"),
        pytest.param("    make lint", False, id="indented-other-target"),
        pytest.param("    remake test", False, id="indented-longer-word"),
        pytest.param("    make TEST_ARGS=-x lint", False, id="indented-assignment"),
    ],
)
def test_an_indented_make_still_runs_the_suite(
    script: str,
    expected: bool,  # noqa: FBT001 - boolean literals clarify parametrized cases.
) -> None:
    """Indentation does not stop a command running.

    The line anchor used to require `make` immediately after the line
    start, so a suite run indented inside a loop or a conditional body
    was invisible to this rule while reading as plainly as any other.
    The negative cases are here for the same reason the positive ones
    are: consuming leading whitespace must not turn an indented
    `make lint` or a `remake` into a suite run.
    """
    assert bool(_make_runs_the_suite(script)) is expected, (
        f"{script!r} should {'' if expected else 'not '}be read as running the "
        f"suite; the targets that do are {sorted(SUITE_MAKE_TARGETS)}"
    )


@pytest.mark.parametrize(
    ("script", "expected"),
    [
        pytest.param("pytest", True, id="column-zero"),
        pytest.param("    pytest", True, id="indented"),
        pytest.param("\tpytest -q", True, id="tab-indented"),
        pytest.param(
            "for d in a b; do\n    pytest\ndone",
            True,
            id="indented-in-a-loop-body",
        ),
        pytest.param(
            "if true; then\n  uv run pytest\nfi",
            True,
            id="indented-behind-uv-run",
        ),
        pytest.param("    cat .pytest_cache", False, id="indented-path-fragment"),
        pytest.param("    ruff check --pytest-style", False, id="indented-option"),
        pytest.param("    mypytest", False, id="indented-longer-word"),
    ],
)
def test_an_indented_pytest_still_runs_the_suite(
    script: str,
    expected: bool,  # noqa: FBT001 - boolean literals clarify parametrized cases.
) -> None:
    """Indentation does not stop a direct `pytest` running either.

    `_MAKE_INVOCATION` already consumed leading whitespace and this
    pattern did not, so an unguarded step whose script read `    pytest`
    restored the duplicate Linux run without failing the rule above. The
    negative cases hold the other direction: consuming the whitespace
    must not turn `.pytest_cache`, a `--pytest-*` option or a longer word
    ending in `pytest` into a suite run.
    """
    assert bool(_PYTEST_INVOCATION.search(script)) is expected, (
        f"{script!r} should {'' if expected else 'not '}be read as invoking "
        "pytest directly"
    )
