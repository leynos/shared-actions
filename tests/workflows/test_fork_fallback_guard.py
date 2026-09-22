"""Contract for the guard that excuses a lane from the fork fallback.

A lane reachable by a fork's pull request normally falls back to a
GitHub-hosted runner, because a fork cannot obtain an Ubicloud runner
and would otherwise queue until the job ceiling; that placement rule is
`test_runner_placement.py`'s. A handful of lanes are excused the
fallback instead, because the hosted runner cannot prove what the job
exists to prove, and they skip a fork's pull request rather than
running on it. This module is the contract for the guard that skip has
to carry.

The guard is asserted by effect, not by substring, because
`true || <guard>` contains the comparison and runs on every fork
regardless of it. Every arm of the condition has to be unreachable by a
fork before the guard is trusted, which is the reading
`_skips_forks` and its helpers below perform.
"""

from __future__ import annotations

import pytest

from . import _workflow_reading as reading


def _strip_expression_wrapper(condition: str) -> str:
    """Return *condition* without its `${{ }}` wrapper and extra spacing.

    A job condition may carry the wrapper or omit it, and the two mean
    the same thing to GitHub.
    """
    text = " ".join(condition.split())
    if text.startswith("${{") and text.endswith("}}"):
        text = text[3:-2].strip()
    return text


def _operands(disjunct: str) -> list[str]:
    """Return the conjoined operands of one arm of a condition."""
    operands = [operand.strip(" ()") for operand in disjunct.split("&&")]
    return [operand for operand in operands if operand]


def _disjunct_requires_the_guard(disjunct: str) -> bool:
    """Return True when one arm requires the head-repository comparison."""
    return reading.FORK_SKIP_GUARD in _operands(disjunct)


def _disjunct_skips_forks(disjunct: str) -> bool:
    """Return True when one arm of a condition cannot admit a fork.

    Either it requires the head-repository comparison, or it is a
    dispatch escape, which no fork reaches.
    """
    if _disjunct_requires_the_guard(disjunct):
        return True
    operands = _operands(disjunct)
    return len(operands) == 1 and operands[0] in reading.FORK_GUARD_ESCAPES


def _skips_forks(condition: str) -> bool:
    """Return True when *condition* genuinely keeps a fork's run away.

    Containing the comparison is not enough, which is what the substring
    check that preceded this helper tested. `true || <guard>` holds the
    comparison and runs on every fork, because the arm beside it is
    always taken.

    So every arm of the condition has to be unreachable by a fork, not
    just one of them. An arm qualifies by requiring the comparison, or
    by being the dispatch escape. A condition with no arms, which is an
    absent `if`, qualifies as nothing: an unguarded job is the default
    this rule exists to refuse.

    A dispatch escape cannot stand alone. Neither `github.event_name !=
    'pull_request'` nor `github.event_name == 'workflow_dispatch'` is
    ever true for a pull request, a fork's and the base repository's
    alike, so a lane carrying one by itself never runs on a pull request
    at all. That satisfies "no fork gets in" by being
    dead, and the exempt lane exists to prove something on an internal
    pull request. At least one arm has to be the guard.
    """
    text = _strip_expression_wrapper(condition)
    disjuncts = [disjunct for disjunct in text.split("||") if disjunct.strip()]
    if not disjuncts:
        return False
    if not all(_disjunct_skips_forks(disjunct) for disjunct in disjuncts):
        return False
    return any(_disjunct_requires_the_guard(disjunct) for disjunct in disjuncts)


class TestForkSkippingLaneCarriesTheGuard:
    """Every lane excused the fallback must actually skip a fork."""

    @pytest.mark.parametrize(
        ("workflow", "job_id"),
        sorted(reading.FORK_FALLBACK_EXEMPTIONS),
        ids=reading.identifier,
    )
    def test_a_fork_skipping_lane_really_skips_forks(
        self, workflow: str, job_id: str
    ) -> None:
        """A lane excused the fallback carries the guard that replaces it.

        Without this the exemption is a note, and deleting the `if` leaves a
        fork's pull request queueing for a runner it cannot have, which is
        the outcome the whole rule exists to prevent.
        """
        job = dict(reading.jobs(workflow))[job_id]
        condition = " ".join(str(job.get("if", "")).split())
        assert _skips_forks(condition), (
            f"{reading.identifier(workflow, job_id)} is excused the fork "
            "fallback because it skips forks, but its condition does not "
            "effectively compare the head repository. Every arm of the "
            "condition must either require the comparison or be one of the "
            f"escapes {sorted(reading.FORK_GUARD_ESCAPES)}, because an "
            "unguarded arm beside the comparison lets a fork through: "
            f"{condition!r}"
        )


class TestForkGuardEffectReading:
    """The guard reader is judged by effect, not by substring."""

    @pytest.mark.parametrize(
        ("condition", "expected"),
        [
            pytest.param(reading.FORK_SKIP_GUARD, True, id="bare"),
            pytest.param("${{ " + reading.FORK_SKIP_GUARD + " }}", True, id="wrapped"),
            pytest.param(
                f"github.event_name == 'push' && {reading.FORK_SKIP_GUARD}",
                True,
                id="conjunction",
            ),
            pytest.param(
                f"{reading.FORK_GUARD_EVENT_ESCAPE} || {reading.FORK_SKIP_GUARD}",
                True,
                id="dispatch-escape-beside-the-guard",
            ),
            pytest.param(
                reading.FORK_GUARD_EVENT_ESCAPE,
                False,
                id="dispatch-escape-alone",
            ),
            pytest.param(
                f"${{{{ {reading.FORK_GUARD_EVENT_ESCAPE} }}}}",
                False,
                id="dispatch-escape-alone-wrapped",
            ),
            pytest.param(
                f"true || {reading.FORK_SKIP_GUARD}",
                False,
                id="always-true-disjunction",
            ),
            pytest.param(
                f"{reading.FORK_SKIP_GUARD} || github.event_name == 'push'",
                False,
                id="unguarded-arm-beside-the-guard",
            ),
            pytest.param(
                f"{reading.FORK_GUARD_EVENT_ESCAPE} || true",
                False,
                id="escape-beside-an-unguarded-arm",
            ),
            pytest.param(
                f"{reading.FORK_GUARD_DISPATCH_ESCAPE} || {reading.FORK_SKIP_GUARD}",
                True,
                id="named-dispatch-escape-beside-the-guard",
            ),
            pytest.param(
                reading.FORK_GUARD_DISPATCH_ESCAPE,
                False,
                id="named-dispatch-escape-alone",
            ),
            pytest.param(
                f"github.event_name == 'pull_request' || {reading.FORK_SKIP_GUARD}",
                False,
                id="a-pull-request-arm-wearing-the-escape-shape",
            ),
            pytest.param("github.event_name == 'push'", False, id="no-guard"),
            pytest.param("", False, id="empty"),
        ],
    )
    def test_the_fork_guard_is_judged_by_effect_not_by_substring(
        self,
        condition: str,
        expected: bool,  # noqa: FBT001 - boolean literals clarify parametrized cases.
    ) -> None:
        """A guard inside a disjunction does not by itself skip forks.

        `true || <guard>` contains the comparison and runs on every fork, so
        a substring check calls it guarded and it is not. That is the
        mutation that defeated the previous assertion. The narrow direction
        matters just as much: this repository's own exempt lane writes
        `<dispatch escape> || <guard>`, which is correct, and a rule that
        refused every disjunction would reject it.

        The escape alone is refused for a different reason from the rest. It
        admits no fork, but only because it admits no pull request at all,
        and a lane that never runs on a pull request cannot be excused the
        fork fallback on the grounds that it skips forks: it has stopped
        proving the thing the exemption was written for.
        """
        assert _skips_forks(condition) is expected, (
            f"{condition!r} was read as "
            f"{'skipping' if not expected else 'not skipping'} forks; expected "
            "the opposite"
        )
