"""Contract for where this repository's own Linux jobs run.

A Linux lane that blocks a developer runs on Ubicloud, because that is
the image this repository's consumers run its actions on, and because a
self-test that only ever proves an action on GitHub's image proves it
somewhere nobody ships. The exceptions are deliberate and each one is
named in `_workflow_reading.HOSTED_LINUX_EXEMPTIONS` with the reason it
is an exception, so that removing a reason is a code change rather than
a silent drift back to the default label.

A fork's pull request cannot obtain an Ubicloud runner, so a lane
reachable by `pull_request` selects its label from the head repository
instead of naming Ubicloud outright; the guard logic behind that
sentence lives in `test_fork_fallback_guard.py`, kept separate because
it is pure condition-string reading rather than placement reading. This
module owns the placement rule itself: which label a job declares, and
whether the shape it declares matches what its triggers require.

Both policies are asserted structurally rather than textually. A
`runs-on` is read as a value and compared to an exact label, never by
prefix or substring: `ubicloud-standard-8` must not satisfy a rule about
`ubicloud-standard-2`, and `actions/cache-audit` must not satisfy a rule
about `actions/cache`. A job is read as a whole, so a job carrying both
`uses` and `runs-on` fails rather than being judged on whichever field
is looked at first.
"""

from __future__ import annotations

import re
import typing as typ

import pytest

from . import _workflow_reading as reading

#: The sanctioned fork fallback, parsed rather than string-compared so
#: that each part can be asserted on its own.
#:
#: A fork's pull request cannot obtain an Ubicloud runner and would
#: queue until the job ceiling, so a lane reachable by `pull_request`
#: selects its label from the head repository. Skipping instead would
#: leave an external contribution with no Linux CI at all.
#:
#: The field path is matched exactly. Swapping `fork` for a sibling such
#: as `private` changes which pull requests fall back and matches
#: nothing here, which is the point: the rule is about forks, not about
#: whichever boolean sits next to it.
_FORK_AWARE_RUNS_ON: typ.Final[re.Pattern[str]] = re.compile(
    r"^\$\{\{\s*github\.event\.pull_request\.head\.repo\.fork"
    r"\s*&&\s*'(?P<fork_arm>[^']+)'"
    r"\s*\|\|\s*'(?P<base_arm>[^']+)'\s*\}\}$"
)


def _triggers(name: str) -> set[str]:
    """Return the event names the workflow *name* is triggered by."""
    on = reading.workflow_document_on(reading.load_workflow(name))
    match on:
        case dict():
            return set(on)
        case list():
            return set(on)
        case str():
            return {on}
        case _:
            return set()


def _fork_arms(runs_on: str) -> tuple[str, str] | None:
    """Return the (fork, non-fork) labels of a fork-aware `runs-on`."""
    match = _FORK_AWARE_RUNS_ON.match(" ".join(runs_on.split()))
    if match is None:
        return None
    return match.group("fork_arm"), match.group("base_arm")


def _expand(value: str) -> set[str]:
    """Return every label a single `runs-on` value can resolve to."""
    if arms := _fork_arms(value):
        return set(arms)
    return {value}


def _matrix_values(job: reading.JobBody, key: str) -> set[str]:
    """Return every value the matrix assigns to *key* in *job*."""
    strategy = job.get("strategy") or {}
    match strategy.get("matrix"):
        case dict() as matrix:
            pass
        case _:
            matrix = {}
    values: set[str] = set()
    direct = matrix.get(key)
    if isinstance(direct, list):
        values.update(str(value) for value in direct)
    for entry in matrix.get("include") or []:
        if isinstance(entry, dict) and key in entry:
            values.add(str(entry[key]))
    return values


def _runner_labels(job: reading.JobBody) -> set[str]:
    """Return every runner label *job* can run on.

    A `runs-on` that defers to the matrix expands to the values the
    matrix supplies for that key, so a matrix job is judged on each arm
    rather than on the expression.
    """
    runs_on = job.get("runs-on")
    if not isinstance(runs_on, str):
        return set()
    if match := reading.MATRIX_REFERENCE.match(runs_on):
        values = _matrix_values(job, match.group(1))
        return {label for value in values for label in _expand(value)}
    return _expand(runs_on)


def _runs_on_values(job: reading.JobBody) -> set[str]:
    """Return *job*'s `runs-on` values before any fork arm is expanded.

    The placement rule is about the shape a lane declares, not only the
    labels it can reach, so it has to see the expression rather than its
    arms.
    """
    runs_on = job.get("runs-on")
    if not isinstance(runs_on, str):
        return set()
    if match := reading.MATRIX_REFERENCE.match(runs_on):
        return _matrix_values(job, match.group(1))
    return {runs_on}


def _is_unparsed_expression(value: str) -> bool:
    """Return True for a `runs-on` expression this module cannot read.

    An expression that is not the sanctioned fork selector could resolve
    to anything, including a paid label on a fork. Treating it as "not a
    Linux lane" and skipping is how the rule gets defeated by a change
    that merely renames the field it keys on, so it is treated as a
    Linux lane and fails.
    """
    return value.strip().startswith("${{") and _fork_arms(value) is None


def _reaches_linux(value: str) -> bool:
    """Return True when a `runs-on` value can land on a Linux runner."""
    if _is_unparsed_expression(value):
        return True
    return bool(_expand(value) & reading.RECOGNIZED_LINUX_LABELS)


class TestJobDeclaresExactlyOneRunnerSource:
    """A job either calls a reusable workflow or names its own runner."""

    @pytest.mark.parametrize(("workflow", "job_id"), reading.all_jobs())
    def test_a_job_either_calls_a_workflow_or_names_a_runner(
        self, workflow: str, job_id: str
    ) -> None:
        """A job declares `uses` or `runs-on`, never both and never neither.

        Reading the two fields independently would accept a job carrying
        both, where the runner label is decoration and the reusable
        workflow decides where the work lands.
        """
        job = dict(reading.jobs(workflow))[job_id]
        has_uses = "uses" in job
        has_runs_on = "runs-on" in job
        assert has_uses != has_runs_on, (
            f"{reading.identifier(workflow, job_id)} declares "
            f"uses={has_uses} and runs-on={has_runs_on}; exactly one is required"
        )


class TestRunnerLabelRecognition:
    """Every declared label is one this repository has decided about."""

    @pytest.mark.parametrize(("workflow", "job_id"), reading.runner_job_ids())
    def test_every_runner_label_is_recognized(self, workflow: str, job_id: str) -> None:
        """Each arm names a label this repository has decided about.

        The recognized sets hold exact tokens, so a shape nobody measured,
        such as `ubicloud-standard-8`, fails here instead of being read as
        "an Ubicloud runner".
        """
        job = dict(reading.jobs(workflow))[job_id]
        labels = _runner_labels(job)
        identifier = reading.identifier(workflow, job_id)
        assert labels, f"{identifier} resolves to no runner label"
        recognized = reading.RECOGNIZED_LINUX_LABELS | reading.RECOGNIZED_OTHER_LABELS
        unrecognized = labels - recognized
        assert not unrecognized, (
            f"{identifier} names unrecognized runner labels "
            f"{sorted(unrecognized)}; add the label to the recognized sets with a "
            "reason, or use one already there"
        )


class TestLinuxPlacementRule:
    """The three shapes a Linux lane's triggers permit, and which applies."""

    @pytest.mark.parametrize(("workflow", "job_id"), reading.runner_job_ids())
    def test_a_linux_lane_declares_the_placement_its_triggers_require(
        self, workflow: str, job_id: str
    ) -> None:
        """Linux work runs on Ubicloud, with a fork fallback where forks reach it.

        Three shapes, and which one a lane must use is decided by its
        triggers rather than by preference. A lane an exemption names stays
        GitHub-hosted. A lane a fork's pull request can reach selects its
        label from the head repository, because a fork cannot obtain an
        Ubicloud runner and would otherwise queue until the ceiling.
        Everything else names Ubicloud outright.
        """
        job = dict(reading.jobs(workflow))[job_id]
        identifier = reading.identifier(workflow, job_id)
        values = {value for value in _runs_on_values(job) if _reaches_linux(value)}
        if not values:
            pytest.skip("no Linux arm")
        if (workflow, job_id) in reading.HOSTED_LINUX_EXEMPTIONS:
            assert values == {reading.HOSTED_LINUX}, (
                f"{identifier} is exempt from the Ubicloud "
                f"rule but declares {sorted(values)}; remove the exemption or "
                f"restore {reading.HOSTED_LINUX!r}"
            )
            return
        if (
            "pull_request" in _triggers(workflow)
            and (workflow, job_id) not in reading.FORK_FALLBACK_EXEMPTIONS
        ):
            for value in values:
                arms = _fork_arms(value)
                assert arms is not None, (
                    f"{identifier} can be reached by a fork's "
                    f"pull request but declares {value!r}; key the label on "
                    "github.event.pull_request.head.repo.fork so that a fork "
                    "falls back rather than queueing for a runner it cannot have"
                )
                assert arms == (reading.HOSTED_LINUX, reading.UBICLOUD_LINUX), (
                    f"{identifier} falls back to {arms[0]!r} "
                    f"and otherwise runs on {arms[1]!r}; the fork arm must be "
                    f"{reading.HOSTED_LINUX!r} and the other "
                    f"{reading.UBICLOUD_LINUX!r}, so that "
                    "a fork never lands on a paid runner"
                )
            return
        assert values == {reading.UBICLOUD_LINUX}, (
            f"{identifier} neither falls back for forks nor "
            f"needs to, so it must name {reading.UBICLOUD_LINUX!r} outright rather "
            f"than {sorted(values)}"
        )


class TestLinuxOnlyLanes:
    """Lanes whose work exists only on Linux stay there on every arm."""

    @pytest.mark.parametrize(("workflow", "job_id"), sorted(reading.LINUX_ONLY_JOBS))
    def test_a_linux_only_lane_reaches_only_linux(
        self, workflow: str, job_id: str
    ) -> None:
        """Every label the lane can resolve to is a recognised Linux label.

        The placement rule above skips a job with no Linux arm, so it
        cannot notice one of these moving to macOS. This rule names them.
        """
        job = dict(reading.jobs(workflow))[job_id]
        labels = {label for value in _runs_on_values(job) for label in _expand(value)}

        assert labels, f"{reading.identifier(workflow, job_id)} declares no runner"
        assert labels <= reading.RECOGNIZED_LINUX_LABELS, (
            f"{reading.identifier(workflow, job_id)} can run on "
            f"{sorted(labels - reading.RECOGNIZED_LINUX_LABELS)}; "
            f"{reading.LINUX_ONLY_JOBS[workflow, job_id]}"
        )


class TestHostedExemptions:
    """Every exemption from the Ubicloud rule names a job that still exists."""

    @pytest.mark.parametrize(
        ("workflow", "job_id"),
        sorted(reading.HOSTED_LINUX_EXEMPTIONS),
        ids=reading.identifier,
    )
    def test_every_exemption_names_a_live_hosted_job(
        self, workflow: str, job_id: str
    ) -> None:
        """An exemption whose job has gone must go with it.

        Without this the mapping accumulates permissions for jobs that no
        longer exist, and the next job to take one of those names inherits a
        decision nobody made about it.
        """
        jobs = dict(reading.jobs(workflow))
        identifier = reading.identifier(workflow, job_id)
        assert job_id in jobs, (
            f"{identifier} is exempt from the Ubicloud rule "
            "but no such job exists; delete the exemption"
        )
        assert reading.HOSTED_LINUX in _runner_labels(jobs[job_id]), (
            f"{identifier} is exempt from the Ubicloud rule "
            f"but no arm runs on {reading.HOSTED_LINUX!r}; delete the exemption"
        )


class TestStepConditionsAvoidRunnerLabels:
    """A step selects a platform by `runner.os`, never by a runner label."""

    @pytest.mark.parametrize(("workflow", "job_id"), reading.runner_job_ids())
    def test_no_step_condition_names_a_runner_label(
        self, workflow: str, job_id: str
    ) -> None:
        """A step selects a platform by `runner.os`, never by a runner label.

        Before this rule, fourteen conditions in `ci.yml` read
        `matrix.os == 'ubuntu-latest'`, so changing the label silently
        switched off every lint, spelling and diagram check on the only leg
        that ran them. The job would have stayed green while doing almost
        nothing. `runner.os` says what the condition means.
        """
        job = dict(reading.jobs(workflow))[job_id]
        recognized = reading.RECOGNIZED_LINUX_LABELS | reading.RECOGNIZED_OTHER_LABELS
        offenders = [
            (step.get("name", "<unnamed>"), condition)
            for step in job.get("steps") or []
            if isinstance(condition := str(step.get("if", "")), str)
            for label in recognized
            if reading.label_token(label).search(condition)
        ]
        assert not offenders, (
            f"{reading.identifier(workflow, job_id)} has step conditions "
            f"naming a runner label: {offenders}; compare `runner.os` instead"
        )
