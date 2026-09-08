"""How the coverage lane reading behaves on inputs chosen for a case.

Separated from `test_coverage_timeout_tiers`, whose subject is this
repository's own workflows. Everything there is skipped while no root
manifest exists, because the action runs `cargo` only where one does,
so the readings behind those assertions are exercised by nothing at
all. These drive them with values chosen rather than found: the four
watchdog sources in precedence order, a blank and a non-positive one, a
job with two coverage steps, a ceiling sitting exactly on its
requirement, and a lane naming a manifest of its own.

Run via ``make test``.
"""

from __future__ import annotations

import typing as typ

import pytest

from tests.workflows.test_coverage_timeout_tiers import (
    COVERAGE_ACTION_SUFFIX,
    MANIFEST_INPUT,
    WATCHDOG_DEFAULT_SECONDS,
    WATCHDOG_INPUT,
    WATCHDOG_VARIABLE,
    WorkflowDocument,
    WorkflowJob,
    WorkflowStep,
    _coverage_lane,
    _coverage_lanes,
    _manifest_inputs,
    _watchdog_of,
    ceiling_is_sufficient,
    required_ceiling_seconds,
)


class TestTheWatchdogIsResolvedAsTheActionResolvesIt:
    """Reading one step's budget out of the four places it can live.

    Every assertion above is skipped while this repository has no root
    manifest, so the reading behind them is exercised here instead,
    against values chosen rather than found. A reading that is wrong
    about a blank source or a zero would otherwise arrive with the
    manifest, unexamined.
    """

    @pytest.mark.parametrize(
        ("step", "job", "document", "expected"),
        [
            pytest.param(
                {"env": {WATCHDOG_VARIABLE: "2400"}},
                {"env": {WATCHDOG_VARIABLE: "1800"}},
                {"env": {WATCHDOG_VARIABLE: "1200"}},
                2400,
                id="the-step-wins",
            ),
            pytest.param(
                {},
                {"env": {WATCHDOG_VARIABLE: "1800"}},
                {"env": {WATCHDOG_VARIABLE: "1200"}},
                1800,
                id="then-the-job",
            ),
            pytest.param(
                {},
                {},
                {"env": {WATCHDOG_VARIABLE: "1200"}},
                1200,
                id="then-the-workflow",
            ),
            pytest.param(
                {"with": {WATCHDOG_INPUT: "900"}},
                {},
                {},
                900,
                id="then-the-action-input",
            ),
            pytest.param(
                {"env": {WATCHDOG_VARIABLE: "2400"}, "with": {WATCHDOG_INPUT: "900"}},
                {},
                {},
                2400,
                id="the-variable-beats-the-input",
            ),
            pytest.param({}, {}, {}, None, id="nothing-sets-one"),
        ],
    )
    def test_the_innermost_source_that_sets_a_budget_wins(
        self,
        step: dict[str, object],
        job: dict[str, object],
        document: dict[str, object],
        expected: int | None,
    ) -> None:
        """Step, then job, then workflow, then the action's own input.

        The order is the action's, not a convenience: a lane that sets
        the variable and passes the input runs on the variable, so a
        contract reading the input first would report a budget the run
        does not use.
        """
        resolved = _watchdog_of(
            typ.cast("WorkflowDocument", document),
            typ.cast("WorkflowJob", job),
            typ.cast("WorkflowStep", step),
        )

        assert resolved == expected, (
            f"step={step!r} job={job!r} document={document!r} must resolve to "
            f"{expected!r}, got {resolved!r}"
        )

    @pytest.mark.parametrize(
        "blank", ["", "   ", "\n"], ids=["empty", "spaces", "a-newline"]
    )
    def test_a_blank_source_falls_through_rather_than_raising(self, blank: str) -> None:
        """A source that says nothing is not a budget of zero.

        This is what a workflow writes when it interpolates an
        expression that resolved to nothing, and it is ordinary rather
        than exotic. Converting it directly raises during collection,
        which loses the lane's name along with the reason.
        """
        resolved = _watchdog_of(
            typ.cast("WorkflowDocument", {"env": {WATCHDOG_VARIABLE: "1200"}}),
            typ.cast("WorkflowJob", {}),
            typ.cast("WorkflowStep", {"env": {WATCHDOG_VARIABLE: blank}}),
        )

        assert resolved == 1200, (
            f"a step setting {blank!r} sets nothing, so the workflow's 1200 "
            f"applies; got {resolved!r}"
        )

    @pytest.mark.parametrize("value", ["0", "-1", " -30 "], ids=str)
    def test_a_non_positive_budget_is_refused(self, value: str) -> None:
        """Zero is not a watchdog, it is the absence of one.

        The action treats a non-positive value as no timeout, so a lane
        carrying one has no third tier while appearing to declare one.
        Returning it would let the arithmetic above certify a lane that
        is unbounded, which is the inversion this contract exists to
        catch.
        """
        with pytest.raises(ValueError, match="positive number of seconds"):
            _watchdog_of(
                typ.cast("WorkflowDocument", {}),
                typ.cast("WorkflowJob", {}),
                typ.cast("WorkflowStep", {"env": {WATCHDOG_VARIABLE: value}}),
            )

    def test_every_coverage_step_in_a_job_is_read(self) -> None:
        """A job running the action twice has two budgets, not one.

        They need not agree, so the lane carries both and the ceiling
        requirement sums them. Reading the first step and multiplying
        describes such a job only when the two happen to match.
        """
        document = typ.cast(
            "WorkflowDocument",
            {
                "jobs": {
                    "build": {
                        "timeout-minutes": 120,
                        "steps": [
                            {
                                "uses": f"{COVERAGE_ACTION_SUFFIX}@abc",
                                "env": {WATCHDOG_VARIABLE: "1800"},
                            },
                            {
                                "uses": f"{COVERAGE_ACTION_SUFFIX}@abc",
                                "env": {WATCHDOG_VARIABLE: "2700"},
                            },
                        ],
                    }
                }
            },
        )

        lane = _coverage_lane("ci.yml", document, "build", document["jobs"]["build"])

        assert lane is not None, "the job invokes the coverage action twice"
        assert lane.watchdogs == (1800, 2700), (
            f"both steps' budgets must be carried, got {lane.watchdogs!r}"
        )


class TestTheCeilingRequirement:
    """The arithmetic the ceiling assertion applies, on chosen numbers.

    The assertion over this repository's own lanes is skipped while
    there is no root manifest, so it certifies nothing about the
    arithmetic today. These drive that arithmetic with workflows written
    for the case, including the equality the README explicitly rejects.
    """

    @staticmethod
    def _document(*, ceiling: int, watchdogs: tuple[int, ...]) -> WorkflowDocument:
        """Return one synthetic workflow with a coverage job.

        Parameters
        ----------
        ceiling : int
            The job's `timeout-minutes`.
        watchdogs : tuple[int, ...]
            One watchdog per coverage step the job runs.

        Returns
        -------
        WorkflowDocument
            A document with a single `build` job.
        """
        return typ.cast(
            "WorkflowDocument",
            {
                "jobs": {
                    "build": {
                        "timeout-minutes": ceiling,
                        "steps": [
                            {
                                "uses": f"{COVERAGE_ACTION_SUFFIX}@abc",
                                "env": {WATCHDOG_VARIABLE: str(watchdog)},
                            }
                            for watchdog in watchdogs
                        ],
                    }
                }
            },
        )

    @pytest.mark.parametrize(
        ("ceiling_minutes", "watchdogs", "sufficient"),
        [
            pytest.param(None, (1800,), False, id="no-ceiling-at-all"),
            pytest.param(40, (1800,), False, id="below-the-requirement"),
            pytest.param(55, (1800,), False, id="exactly-on-the-requirement"),
            pytest.param(56, (1800,), True, id="one-minute-above-it"),
            pytest.param(100, (1800, 2700), False, id="two-steps-below-the-sum"),
            pytest.param(101, (1800, 2700), True, id="two-steps-above-the-sum"),
        ],
    )
    def test_the_ceiling_rule_judges_a_parsed_lane(
        self,
        ceiling_minutes: int | None,
        watchdogs: tuple[int, ...],
        *,
        sufficient: bool,
    ) -> None:
        """The rule the contract applies, over lanes this repository lacks.

        Every assertion in the contract skips while no root
        ``Cargo.toml`` exists, so the arithmetic behind them is exercised
        by nothing there. These drive the same predicate the contract
        calls, over documents parsed the same way, so the equality case
        the README rejects by name is checked rather than restated.

        The 55-minute case is the one that matters: 1,800 s of watchdog,
        600 s of measured work outside it and a 900 s margin is exactly
        3,300 s, so a ceiling of 55 minutes sits on its requirement and
        an inclusive comparison would pass it.
        """
        document = self._document(ceiling=ceiling_minutes, watchdogs=watchdogs)
        (lane,) = _coverage_lanes({"ci.yml": document})
        budgets = [
            watchdog if watchdog is not None else WATCHDOG_DEFAULT_SECONDS
            for watchdog in lane.watchdogs
        ]

        assert ceiling_is_sufficient(lane.ceiling, budgets) is sufficient, (
            f"a ceiling of {ceiling_minutes} minutes against watchdogs "
            f"{watchdogs} must {'clear' if sufficient else 'fail'} the "
            f"{required_ceiling_seconds(budgets)} s requirement; the rule is "
            f"strictly above, because a ceiling on its requirement cancels the "
            f"job at the moment the watchdog would have reported the overrun"
        )

    def test_two_steps_require_the_sum_rather_than_a_multiple(self) -> None:
        """Unequal budgets are why the rule sums rather than multiplies.

        A job running the action twice with 1,800 s and 2,700 s needs
        4,500 s of watchdog. Multiplying the first step's budget by the
        step count asks for 3,600, which is less, so a lane sized that
        way would be certified while being able to overrun its ceiling.
        """
        (lane,) = _coverage_lanes(
            {"ci.yml": self._document(ceiling=120, watchdogs=(1800, 2700))}
        )

        budgets = [watchdog for watchdog in lane.watchdogs if watchdog is not None]

        assert sum(budgets) == 4500, f"the sum of both budgets, got {budgets}"
        assert sum(budgets) != budgets[0] * len(budgets), (
            "this case exists because the multiplication and the sum differ"
        )

    def test_a_lane_naming_a_manifest_is_detected(self) -> None:
        """No lane here passes one, so the reading needs its own case.

        The assertion over this tree is satisfied by a reading that
        never looks at the input at all, since nothing sets it. Driving
        the reading with a lane that does is the only way to show it
        would notice the second route to a `cargo` run.
        """
        document = typ.cast(
            "WorkflowDocument",
            {
                "jobs": {
                    "build": {
                        "timeout-minutes": 120,
                        "steps": [
                            {
                                "uses": f"{COVERAGE_ACTION_SUFFIX}@abc",
                                "with": {MANIFEST_INPUT: "crates/thing/Cargo.toml"},
                            }
                        ],
                    }
                }
            },
        )

        named = [manifest for _, manifest in _manifest_inputs({"ci.yml": document})]

        assert named == ["crates/thing/Cargo.toml"], (
            f"a lane passing {MANIFEST_INPUT} must be seen, got {named!r}"
        )
