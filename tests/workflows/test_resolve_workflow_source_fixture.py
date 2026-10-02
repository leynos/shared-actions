"""Contract that the resolve fixture's two branches stay disjoint.

`test-resolve-workflow-source.yml` is the only thing that exercises the
OIDC fail-fast of `resolve-workflow-source`, and it can only do so under
`workflow_dispatch`. Act forces `ACT=true` into a composite's own
environment whatever an outer step declares, so under act the branch is
unreachable and the two halves can never run in the same run.

The fixture used to say so with a job-level `env: ACT: "false"`, which is
not how either runner works. GitHub never sets `ACT` anywhere, so the
step it guarded ran on a dispatch and failed the job ahead of the branch
it was meant to leave room for; act merges a step's `env` into that
step's guard and overwrites `ACT` before the composite reads it, so the
value never reached the action either way. The fixture could not be both
green and meaningful.

So the separation is made where a runner evaluates it -- in the guards
themselves -- and this module holds it there, by evaluating those guards
rather than by matching their text.
"""

from __future__ import annotations

import re
import typing as typ

import pytest

from . import _workflow_reading as reading

if typ.TYPE_CHECKING:
    import collections.abc as cabc

#: The fixture under contract, its job, and the two guarded steps.
WORKFLOW: typ.Final[str] = "test-resolve-workflow-source.yml"
JOB: typ.Final[str] = "resolve"
ACT_STEP: typ.Final[str] = "act-branch"
OIDC_STEP: typ.Final[str] = "oidc-branch"

#: The one condition form the guards may use: a comparison against `ACT`.
#: Deliberately narrow. A guard written any other way is refused rather
#: than evaluated, because a reader that guessed at a form it did not
#: understand would report a separation the runner does not implement.
_GUARD: typ.Final[re.Pattern[str]] = re.compile(
    r"^\$\{\{\s*env\.ACT\s*(?P<operator>==|!=)\s*'(?P<value>[^']*)'\s*\}\}$"
)

#: The environment each runner evaluates the guards in. GitHub sets no
#: `ACT` at all; act sets it to the literal `true`.
_UNDER_ACT: typ.Final[cabc.Mapping[str, str]] = {"ACT": "true"}
_ON_DISPATCH: typ.Final[cabc.Mapping[str, str]] = {}


def _document() -> dict[typ.Hashable, object]:
    """Return the fixture document, typed for the keys read here."""
    return typ.cast("dict[typ.Hashable, object]", reading.load_workflow(WORKFLOW))


def _job() -> cabc.Mapping[str, object]:
    """Return the fixture job's body."""
    return dict(reading.jobs(WORKFLOW))[JOB]


def _steps() -> list[cabc.Mapping[str, object]]:
    """Return the fixture job's steps, in document order."""
    return list(_job().get("steps") or [])


def _step(identifier: str) -> cabc.Mapping[str, object]:
    """Return the fixture step declaring *identifier* as its id.

    Raises
    ------
    AssertionError
        If no step declares it. A renamed id would leave the assertion
        step reading an outcome that is always empty.
    """
    found = [step for step in _steps() if step.get("id") == identifier]
    if len(found) != 1:
        msg = f"{WORKFLOW} must have exactly one step with id {identifier!r}"
        raise AssertionError(msg)
    return found[0]


def selects(condition: object, environment: cabc.Mapping[str, str]) -> bool:
    """Return whether *condition* selects its step in *environment*.

    The environment is what act builds for a step: the ambient one with
    the step's own `env` merged over it. A condition outside `_GUARD`'s
    form raises rather than returning a default, because the caller is
    asking whether a guard separates the two branches and "unreadable"
    is not an answer to that.

    Raises
    ------
    AssertionError
        If *condition* is absent or is not a comparison against `ACT`.
    """
    match = _GUARD.fullmatch(str(condition))
    if match is None:
        msg = (
            f"{WORKFLOW}: {condition!r} is not an `env.ACT` comparison, so "
            "whether it separates the fixture's branches cannot be read here"
        )
        raise AssertionError(msg)
    equal = environment.get("ACT", "") == match["value"]
    return equal if match["operator"] == "==" else not equal


def _environment(step: cabc.Mapping[str, object], ambient: dict[str, str]) -> dict[str, str]:
    """Return *ambient* with the step's own `env` merged over it."""
    declared = step.get("env") or {}
    if not isinstance(declared, dict):  # pragma: no cover - malformed fixture
        msg = f"{WORKFLOW}: step env is not a mapping: {declared!r}"
        raise AssertionError(msg)
    return {**ambient, **{str(name): str(value) for name, value in declared.items()}}


@pytest.mark.parametrize(
    ("runner", "ambient", "runs", "skips"),
    [
        pytest.param("act", _UNDER_ACT, ACT_STEP, OIDC_STEP, id="under-act"),
        pytest.param(
            "a dispatch", _ON_DISPATCH, OIDC_STEP, ACT_STEP, id="on-a-dispatch"
        ),
    ],
)
def test_each_runner_selects_exactly_one_half(
    runner: str,
    ambient: dict[str, str],
    runs: str,
    skips: str,
) -> None:
    """One guard fires and the other does not, in either runner.

    The guards are read off the steps rather than from the job, so a
    guard moved up a level fails here: a job-level condition is
    evaluated against the job's environment, which a step's `env` never
    reaches, and act's own `ACT` write happens below it.
    """
    selected = {
        identifier: selects(_step(identifier).get("if"), _environment(_step(identifier), ambient))
        for identifier in (ACT_STEP, OIDC_STEP)
    }

    assert selected[runs], f"{WORKFLOW}::{runs} must run {runner}"
    assert not selected[skips], f"{WORKFLOW}::{skips} must be skipped {runner}"


def test_the_guards_are_not_shadowed_above_the_step() -> None:
    """Neither the job nor the workflow guards on `ACT`.

    A condition at either level is evaluated before a step's `env` is
    merged, so it would select or skip the whole job and leave the
    step-level guards to decide nothing.
    """
    offenders = [
        where
        for where, scope in (("the job", _job()), ("the workflow", _document()))
        if "ACT" in str(scope.get("if", ""))
    ]

    assert not offenders, (
        f"{WORKFLOW} guards on ACT in {offenders}; the guards belong on the "
        "steps, where act merges each step's own env before evaluating them"
    )


def test_no_step_sets_act_for_itself() -> None:
    """No step declares `ACT` in its own `env`.

    act evaluates a step's `if:` against that step's own `env`, so
    `env: ACT:` would be read by the guard above it and select the step
    it was written to skip -- and act overwrites `ACT` before a
    composite's inner step reads it, so it could not take effect anyway.
    This is the clause that keeps the fixture from sliding back to the
    shape that made the OIDC half dead in every environment.
    """
    offenders = {
        str(step.get("name")): step["env"]["ACT"]
        for step in _steps()
        if isinstance(step.get("env"), dict) and "ACT" in step["env"]
    }

    assert not offenders, (
        f"these steps set ACT in their own env {offenders}; act feeds a "
        "step's env to that step's own `if:` and overwrites ACT before a "
        "composite reads it, so the value can only mislead the guard"
    )


def test_the_step_reporting_the_result_runs_in_either_runner() -> None:
    """The last step is unguarded, because both arms must be reported.

    It is the only step that says which branch ran, and its two arms are
    complementary: each names the step that must have succeeded and the
    other that must have been skipped. A condition on it would leave one
    of the two branches unasserted while the run stayed green.
    """
    last = _steps()[-1]

    assert "if" not in last, (
        f"{WORKFLOW}: {last.get('name')!r} reports which branch ran and must "
        "run in both runners, so it carries no condition"
    )
    assert str(last.get("name", "")).startswith("Assert"), (
        f"{WORKFLOW}: the last step is expected to be the outcome assertion, "
        f"found {last.get('name')!r}"
    )
