"""Contract that `ci.yml`'s platform-specific steps run where they must.

The matrix leg selects its per-platform steps with `runner.os`. A rule
that only refuses runner labels in those conditions, which
`TestStepConditionsAvoidRunnerLabels` does, passes a condition turned to
`runner.os == 'Windows'`, `false`, or deleted outright: the lint,
spelling and diagram checks would stop running on Linux, or the suite
would start running there twice, and the job would stay green. So each
conditioned step is pinned to its platform here, by meaning rather than
by text: its condition must carry exactly one `runner.os` comparison,
naming that platform, as a top-level conjunct, with no disjunction or
negation that could admit another runner or none.
"""

from __future__ import annotations

import re
import typing as typ

import pytest

from . import _workflow_reading as reading

if typ.TYPE_CHECKING:
    import collections.abc as cabc

#: Every step in `ci.yml::python-tests` that runs on one platform only,
#: with that platform. The matrix has a Linux and a macOS leg.
PLATFORM_STEPS: typ.Final[cabc.Mapping[str, str]] = {
    "Install nfpm": "Linux",
    "Setup Bun": "Linux",
    "Setup Bun (macOS)": "macOS",
    "Install mdtablefix": "Linux",
    "Check formatting": "Linux",
    "Enforce en-GB-oxendict spelling": "Linux",
    "Markdown lint": "Linux",
    "Install Merman CLI": "Linux",
    "Install Nixie": "Linux",
    "Run docstring examples": "Linux",
    "Validate Mermaid diagrams": "Linux",
    "Setup Rust": "Linux",
    "Install Whitaker": "Linux",
    "Run lint checks": "Linux",
    "Install Makefile parser": "macOS",
    "Run tests": "macOS",
}

_RUNNER_OS_COMPARISON: typ.Final[re.Pattern[str]] = re.compile(
    r"runner\.os\s*(==|!=)\s*'([^']*)'"
)


def platform_of(condition: str) -> str | None:
    """Return the platform *condition* confines a step to, or None.

    The condition must be a conjunction whose conjuncts include exactly
    one `runner.os == '<platform>'`. A disjunction or a negation can
    admit another runner, a second `runner.os` comparison can contradict
    the first, and a literal `false` conjunct disables the step on every
    runner, so any of them yields None.
    """
    text = " ".join(condition.removeprefix("${{").removesuffix("}}").split())
    if "||" in text or "!" in text.replace("!=", ""):
        return None
    if any(part.strip().lower() == "false" for part in text.split("&&")):
        return None
    comparisons = [
        _RUNNER_OS_COMPARISON.fullmatch(part.strip())
        for part in text.split("&&")
        if "runner.os" in part
    ]
    if len(comparisons) != 1 or comparisons[0] is None:
        return None
    operator, platform = comparisons[0].groups()
    return platform if operator == "==" else None


def _python_tests_steps() -> list[cabc.Mapping[str, object]]:
    """Return the steps of `ci.yml::python-tests`."""
    job = dict(reading.jobs("ci.yml"))["python-tests"]
    return list(job.get("steps") or [])


def test_every_platform_step_is_confined_to_its_platform() -> None:
    """Each pinned step runs on its platform and on no other."""
    found = {
        str(step.get("name")): platform_of(str(step.get("if", "")))
        for step in _python_tests_steps()
        if str(step.get("name")) in PLATFORM_STEPS
    }

    missing = sorted(set(PLATFORM_STEPS) - set(found))
    assert not missing, f"ci.yml::python-tests lost pinned steps: {missing}"
    wrong = {name: got for name, got in found.items() if got != PLATFORM_STEPS[name]}
    assert not wrong, (
        f"these steps are not confined to their platform (read as {wrong}); "
        f"each must carry `runner.os == '<platform>'` as a conjunct: {PLATFORM_STEPS}"
    )


def test_no_other_step_selects_a_platform() -> None:
    """A step outside the pinned set may not select a platform unnoticed."""
    extra = [
        str(step.get("name"))
        for step in _python_tests_steps()
        if "runner.os" in str(step.get("if", ""))
        and str(step.get("name")) not in PLATFORM_STEPS
    ]
    assert not extra, (
        f"ci.yml::python-tests has platform-conditioned steps {extra} that "
        "PLATFORM_STEPS does not pin; add each with its platform"
    )


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        pytest.param("runner.os == 'Linux'", "Linux", id="plain"),
        pytest.param("${{ runner.os == 'macOS' }}", "macOS", id="wrapped"),
        pytest.param(
            "runner.os == 'Linux' && steps.x.outputs.cache-hit != 'true'",
            "Linux",
            id="with-a-cache-conjunct",
        ),
        pytest.param("runner.os == 'Windows'", "Windows", id="another-platform"),
        pytest.param("false", None, id="literal-false"),
        pytest.param(
            "runner.os == 'Linux' && false", None, id="false-conjunct-disables"
        ),
        pytest.param(
            "${{ runner.os == 'Linux' && FALSE }}", None, id="false-conjunct-wrapped"
        ),
        pytest.param("", None, id="absent"),
        pytest.param("runner.os != 'macOS'", None, id="negated-comparison"),
        pytest.param("runner.os == 'Linux' || true", None, id="disjunction"),
        # The discriminating form: every conjunct stays whole, and only the
        # disjunction hidden in the last one would admit every runner.
        pytest.param(
            "runner.os == 'Linux' && github.ref != '' || true",
            None,
            id="disjunction-inside-a-conjunct",
        ),
        pytest.param("!(runner.os == 'Linux')", None, id="negation"),
        pytest.param(
            "runner.os == 'Linux' && runner.os == 'macOS'",
            None,
            id="contradictory",
        ),
    ],
)
def test_platform_of_reads_only_a_confining_condition(
    condition: str, expected: str | None
) -> None:
    """Only a conjunction with one positive `runner.os` comparison confines."""
    assert platform_of(condition) == expected, (
        f"{condition!r} read as {platform_of(condition)!r}, expected {expected!r}"
    )
