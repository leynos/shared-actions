"""Contract for the timeout tiers on this repository's own coverage lanes.

This repository is the reference for the four-tier rule, so its own
lanes have to obey it. They currently obey it vacuously: `detect.py`
classifies this project as Python, because there is no root
`Cargo.toml`, and `generate-coverage` gates every Rust step on that
detection. No `cargo` invocation happens, so the wall-clock watchdog
never runs and the two nextest tiers do not apply either.

That is worth asserting rather than assuming, because the transition is
silent and the shape it lands in is the one the rule exists to prevent.
Adding a root `Cargo.toml` would flip the detection, start running
`cargo` under the action's undocumented 1,800 s default, and leave both
coverage jobs with a 30 minute ceiling: a ceiling equal to the watchdog,
so the watchdog could never fire and every overrun would be a cancelled
job with no log. That is the defect a rules audit found in `wildside`,
and it would arrive here through a change that mentions timeouts
nowhere.

So this module asserts the precondition and the consequence together. If
the manifest appears, the lanes must already carry a written watchdog
and a ceiling above it, and this file's failure says so.

See "The cargo watchdog" in
`.github/actions/generate-coverage/README.md` for the canonical rule.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest
import yaml

if typ.TYPE_CHECKING:
    import collections.abc as cabc

REPOSITORY_ROOT: typ.Final[Path] = Path(__file__).resolve().parents[2]
WORKFLOWS_DIRECTORY: typ.Final[Path] = REPOSITORY_ROOT / ".github" / "workflows"

#: The manifest whose presence flips `detect.py` from Python to Rust or
#: mixed, and so decides whether any of the cargo tiers apply.
ROOT_MANIFEST: typ.Final[Path] = REPOSITORY_ROOT / "Cargo.toml"

#: The action, referenced from within this repository, so a local `uses:`
#: matches as well as the fully qualified form a consumer writes.
COVERAGE_ACTION_SUFFIX: typ.Final[str] = ".github/actions/generate-coverage"

#: The environment variable the action reads for its wall-clock cap, and
#: the input that carries the same budget.
WATCHDOG_VARIABLE: typ.Final[str] = "RUN_RUST_CARGO_WAIT_TIMEOUT"
WATCHDOG_INPUT: typ.Final[str] = "cargo-wait-timeout"

#: The action's other route to a `cargo` run. `detect.py` prefers a root
#: manifest, but falls back to this input when one is named and exists,
#: so the tiers below apply to a lane that passes it even where the root
#: manifest is absent.
MANIFEST_INPUT: typ.Final[str] = "cargo-manifest"

#: The action's own default, in seconds. Named here so the failure
#: message can say what a lane would inherit rather than only that it
#: inherits something.
WATCHDOG_DEFAULT_SECONDS: typ.Final[int] = 1800

#: Everything in a coverage job that is not the `cargo` invocation the
#: watchdog would bound. Measured from the worst of several runs, and
#: from runs of every conclusion rather than the successful ones alone: a
#: run terminated by a timeout is the strongest evidence an allowance was
#: too small, and excluding it reproduces the failure it recorded.
#:
#: Across fourteen runs of each workflow, successful and cancelled, the
#: widest gap between the coverage step and its job was 50 s on run
#: 34033536360 and 43 s on run 34064475599. None was terminated by a
#: timeout. Ten minutes is twelve times the worse of those.
OUTSIDE_WATCHDOG_ALLOWANCE_SECONDS: typ.Final[int] = 10 * 60


#: The margin a ceiling must carry above the sum it contains. A ceiling
#: equal to that sum cancels the job at the moment the watchdog would
#: have reported the overrun, and the report is the only thing that makes
#: an overrun actionable, so reaching the requirement buys nothing.
CEILING_MARGIN_SECONDS: typ.Final[int] = 15 * 60


def required_ceiling_seconds(budgets: cabc.Sequence[int]) -> int:
    """Return the smallest ceiling that does not pre-empt the watchdogs.

    Three terms. Every coverage step may legitimately spend its whole
    watchdog, so the budgets are summed rather than one of them
    multiplied by their count: they need not agree, and multiplying the
    first understates a job whose second step is given more. The measured
    work outside those windows is added because the job timer covers it
    and the watchdogs do not. The margin is added because a ceiling that
    merely reaches this sum cancels the job at the moment the watchdog
    would have reported the overrun.

    Parameters
    ----------
    budgets : cabc.Sequence[int]
        One watchdog budget per coverage step, in seconds.

    Returns
    -------
    int
        The requirement in seconds.

    Examples
    --------
    >>> required_ceiling_seconds([1800, 2700])
    6000
    """
    return sum(budgets) + OUTSIDE_WATCHDOG_ALLOWANCE_SECONDS + CEILING_MARGIN_SECONDS


def ceiling_is_sufficient(
    ceiling_minutes: int | None, budgets: cabc.Sequence[int]
) -> bool:
    """Return whether a job's ceiling clears its requirement.

    Strictly above, not at: a ceiling sitting exactly on the requirement
    is the case the README rejects by name, because the job is cancelled
    at the moment the watchdog would have reported the overrun and a
    cancellation discards the log.

    The rule lives here rather than inline in an assertion so it can be
    exercised against lanes this repository does not have. Its own lanes
    skip while no root ``Cargo.toml`` exists, so an inline comparison
    would be checked by nothing at all.

    Parameters
    ----------
    ceiling_minutes : int or None
        The job's ``timeout-minutes``, or None when it declares none and
        so inherits GitHub's six-hour default.
    budgets : cabc.Sequence[int]
        One watchdog budget per coverage step, in seconds.

    Returns
    -------
    bool
        True when the ceiling is declared and strictly above the
        requirement.

    Examples
    --------
    >>> ceiling_is_sufficient(None, [1800])
    False
    >>> ceiling_is_sufficient(55, [1800])
    False
    >>> ceiling_is_sufficient(56, [1800])
    True
    """
    if ceiling_minutes is None:
        return False
    return ceiling_minutes * 60 > required_ceiling_seconds(budgets)


class WorkflowStep(typ.TypedDict, total=False):
    """One step of a job, declaring only the keys these tests read.

    Attributes
    ----------
    uses : str
        The action the step invokes, when it invokes one.
    env : dict[str, object]
        The step-level environment, the innermost watchdog scope.
    with_ : dict[str, object]
        The action's inputs. Spelled ``with`` in YAML, which is a Python
        keyword, so the mapping is read by key rather than by attribute.
    """

    uses: str
    env: dict[str, object]


class WorkflowJob(typ.TypedDict, total=False):
    """One job of a workflow, declaring only the keys these tests read.

    Attributes
    ----------
    timeout-minutes : int
        The job's ceiling, the outermost tier.
    env : dict[str, object]
        The job-level environment, consulted when the step sets nothing.
    steps : list[WorkflowStep]
        The job's steps, in the order it runs them.
    """

    env: dict[str, object]
    steps: list[WorkflowStep]


class WorkflowDocument(typ.TypedDict, total=False):
    """One workflow file, declaring only the keys these tests read.

    Attributes
    ----------
    env : dict[str, object]
        The workflow-level environment, the outermost watchdog scope.
    jobs : dict[str, WorkflowJob]
        The workflow's jobs, keyed by identifier.
    """

    env: dict[str, object]
    jobs: dict[str, WorkflowJob]


class CoverageLane(typ.NamedTuple):
    """One job invoking the coverage action, with its budgets.

    Attributes
    ----------
    workflow : str
        The workflow file's name.
    job : str
        The job's identifier.
    watchdogs : tuple[int | None, ...]
        The budget in force for each coverage step the job runs, in
        order, each resolved from that step's environment, the job's,
        the workflow's, or that step's ``cargo-wait-timeout`` input.
        None means the step would inherit the action's default.

        A tuple rather than one value because the budgets need not
        agree: the variable resolves per step, so a job running the
        action twice can raise it for the feature set that builds more,
        and its ceiling has to contain the sum of what it actually set
        rather than a multiple of whichever step was read first.
    ceiling : int or None
        The job's ``timeout-minutes``, or None when it declares none.
    """

    workflow: str
    job: str
    watchdogs: tuple[int | None, ...]
    ceiling: int | None

    def __str__(self) -> str:
        """Return a location suitable for a failure message.

        Returns
        -------
        str
            ``workflow:job`` for this lane.
        """
        return f"{self.workflow}:{self.job}"


def _budget_from(raw: object) -> int | None:
    """Return one source's watchdog budget, or None when it sets none.

    A blank or whitespace-only value is not a budget of zero, it is a
    source that says nothing, so it falls through to the next one. That
    is what a workflow writes when it interpolates an expression that
    resolved to nothing, and converting it directly raises before the
    contract can say which lane was at fault.

    Zero and negative values are refused rather than returned. The
    action treats them as no timeout at all, so a lane carrying one has
    no third tier while appearing to declare one, which is the inversion
    this contract exists to catch rather than to propagate.

    Parameters
    ----------
    raw : object
        The value a workflow set, as the YAML parser returned it.

    Returns
    -------
    int or None
        The budget in seconds, or None when the source sets none.

    Raises
    ------
    ValueError
        If the value is present and non-blank but not a whole number of
        seconds, or is not positive.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    seconds = int(text)
    if seconds <= 0:
        message = (
            f"{WATCHDOG_VARIABLE} must be a positive number of seconds; "
            f"{raw!r} would leave the cargo invocation unbounded while "
            f"appearing to bound it"
        )
        raise ValueError(message)
    return seconds


def _watchdog_of(
    document: WorkflowDocument, job: WorkflowJob, step: WorkflowStep
) -> int | None:
    """Return the watchdog budget in force for one coverage step.

    All three environment levels are read, innermost first, as GitHub
    resolves them, and the action's input last: the variable takes
    precedence over the input, so a lane setting both runs on the
    variable. A level that sets the name to a blank value is treated as
    setting nothing, so resolution continues rather than stopping at a
    source that says nothing.

    Parameters
    ----------
    document : WorkflowDocument
        The whole workflow document.
    job : WorkflowJob
        The enclosing job.
    step : WorkflowStep
        The coverage step.

    Returns
    -------
    int or None
        The budget in seconds, or None when nothing sets one.
    """
    levels = (step.get("env"), job.get("env"), document.get("env"))
    for source in levels:
        if not isinstance(source, dict):
            continue
        budget = _budget_from(source.get(WATCHDOG_VARIABLE))
        if budget is not None:
            return budget
    inputs = step.get("with")
    if isinstance(inputs, dict):
        return _budget_from(inputs.get(WATCHDOG_INPUT))
    return None


def workflow_documents() -> dict[str, WorkflowDocument]:
    """Return every workflow document, keyed by file name.

    This is the one place these tests touch the filesystem or the YAML
    parser, so an unreadable or unparsable workflow fails here rather
    than inside an assertion about budgets. Both workflow extensions
    are read: a lane in the other one would otherwise escape every
    assertion here without failing anything.

    Returns
    -------
    dict[str, WorkflowDocument]
        File name to parsed document.
    """
    documents: dict[str, WorkflowDocument] = {}
    for pattern in ("*.yml", "*.yaml"):
        for path in sorted(WORKFLOWS_DIRECTORY.glob(pattern)):
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
            if isinstance(document, dict):
                documents[path.name] = document
    return documents


def _declared_jobs(
    documents: dict[str, WorkflowDocument] | None = None,
) -> list[tuple[str, WorkflowDocument, str, WorkflowJob]]:
    """Return every job in every workflow, with its file and document.

    The documents are a parameter so the reading can be driven with
    workflows written for a case rather than found in the tree. Reading
    the repository's own is the default rather than the only option,
    which keeps the filesystem access at one named boundary instead of
    inside the derivations.

    The job's identity travels with it rather than being reconstructed
    from an enclosing loop, which is what lets the lane building be a
    single comprehension.

    Returns
    -------
    list of tuple
        Workflow name, document, job identifier, and job.
    """
    return [
        (workflow, document, str(name), job)
        for workflow, document in (documents or workflow_documents()).items()
        for name, job in (document.get("jobs") or {}).items()
        if isinstance(job, dict)
    ]


def _coverage_steps(job: WorkflowJob) -> list[WorkflowStep]:
    """Return the steps in one job that invoke the coverage action.

    Parameters
    ----------
    job : WorkflowJob
        The parsed job.

    Returns
    -------
    list[WorkflowStep]
        The matching steps, in the order the job runs them.
    """
    steps = job.get("steps")
    if not isinstance(steps, list):
        return []
    return [
        step
        for step in steps
        if isinstance(step, dict)
        and COVERAGE_ACTION_SUFFIX in str(step.get("uses", ""))
    ]


def _coverage_lane(
    workflow: str,
    document: WorkflowDocument,
    job_name: str,
    job: WorkflowJob,
) -> CoverageLane | None:
    """Return one job's lane, or None when it runs no coverage step.

    Parameters
    ----------
    workflow : str
        The workflow file's name.
    document : WorkflowDocument
        The enclosing document, read for a workflow-level watchdog.
    job_name : str
        The job's identifier.
    job : WorkflowJob
        The parsed job.

    Returns
    -------
    CoverageLane or None
        The lane, or None when the job invokes no coverage step.
    """
    steps = _coverage_steps(job)
    if not steps:
        return None
    raw = job.get("timeout-minutes")
    return CoverageLane(
        workflow=workflow,
        job=job_name,
        watchdogs=tuple(_watchdog_of(document, job, step) for step in steps),
        ceiling=None if raw is None else int(str(raw)),
    )


def _coverage_lanes(
    documents: dict[str, WorkflowDocument] | None = None,
) -> tuple[CoverageLane, ...]:
    """Return every job invoking the coverage action, with its budgets.

    This is the one place these tests touch the filesystem or the YAML
    parser, so an unreadable or unparsable workflow fails here rather
    than inside an assertion about budgets. Both workflow extensions
    are read: a lane in the other one would otherwise escape every
    assertion here without failing anything.

    Returns
    -------
    tuple[CoverageLane, ...]
        One entry per coverage-invoking job.
    """
    return tuple(
        lane
        for workflow, document, job_name, job in _declared_jobs(documents)
        if (lane := _coverage_lane(workflow, document, job_name, job)) is not None
    )


def _manifest_inputs(
    documents: dict[str, WorkflowDocument] | None = None,
) -> list[tuple[CoverageLane, str]]:
    """Return each coverage step's ``cargo-manifest`` input, if any.

    Parameters
    ----------
    documents : dict[str, WorkflowDocument] or None
        Parsed workflows keyed by file name. When None, the
        repository's own are read.

    Returns
    -------
    list of tuple
        The lane and the manifest it names, once per coverage step.
    """
    return [
        (lane, manifest)
        for workflow, document, job_name, job in _declared_jobs(documents)
        if (lane := _coverage_lane(workflow, document, job_name, job)) is not None
        for manifest in _named_manifests(job)
    ]


def _named_manifests(job: WorkflowJob) -> list[str]:
    """Return the ``cargo-manifest`` each coverage step in one job names.

    Parameters
    ----------
    job : WorkflowJob
        The parsed job.

    Returns
    -------
    list[str]
        One entry per coverage step, empty string where it names none.
    """
    return [
        str(inputs.get(MANIFEST_INPUT, "")).strip()
        for step in _coverage_steps(job)
        if isinstance(inputs := step.get("with"), dict)
    ]


class TestCoverageTimeoutTiers:
    """The four tiers, as they stand in this repository's own workflows.

    Two of them are inert here and asserted anyway. The action runs
    `cargo` only where a root manifest exists, and this repository has
    none, so the watchdog and the nextest budgets bound nothing today.
    The tests are written now so the requirement arrives with the
    manifest rather than after the first run it kills.
    """

    def test_this_repository_invokes_its_own_coverage_action(self) -> None:
        """The contract needs a lane to assert against.

        A rename or a move that stopped the coordinate matching would
        otherwise turn every assertion below into a vacuous pass over an
        empty list, and the loss would look exactly like success.
        """
        assert _coverage_lanes(), (
            f"no workflow job uses {COVERAGE_ACTION_SUFFIX}; either coverage moved "
            f"or this contract stopped recognizing it"
        )

    def test_no_lane_names_a_manifest_of_its_own(self) -> None:
        """The root manifest is not the only route to a `cargo` run.

        `detect.py` prefers a root ``Cargo.toml`` but falls back to the
        ``cargo-manifest`` input when one is named and exists. A lane
        passing it runs cargo with no root manifest present, so the
        precondition below would skip the very tiers that had become
        real. This asserts the second route is closed as well as the
        first.
        """
        naming = [
            f"{lane}: {MANIFEST_INPUT}={manifest!r}"
            for lane, manifest in _manifest_inputs()
            if manifest
        ]
        assert not naming, (
            f"these lanes pass {MANIFEST_INPUT}, so generate-coverage may run "
            f"cargo here whatever the repository root holds, and the tiers "
            f"below stop being inert: {naming}"
        )

    def test_the_cargo_tiers_do_not_apply_until_a_manifest_appears(self) -> None:
        """The precondition, asserted rather than assumed.

        `detect.py` classifies this project by the presence of a root
        `Cargo.toml`, and the action gates every Rust step on that. Without
        the manifest no `cargo` runs, so the watchdog and both nextest tiers
        are inert here however the lanes are configured.

        The failure message is the point of the test: it fires on the change
        that adds the manifest, which is a change about packaging rather
        than about timeouts, and tells its author what else has to move.
        """
        assert not ROOT_MANIFEST.is_file(), (
            "a root Cargo.toml has appeared, so generate-coverage now runs cargo "
            "here and the tiers below apply. Before this lands: set "
            f"{WATCHDOG_VARIABLE} on every coverage job from measured runs, raise "
            "each job's timeout-minutes above that plus the work outside the "
            "watchdog's window, set a per-test slow-timeout and a global-timeout "
            "in .config/nextest.toml, and update the four-tier section in "
            ".github/actions/generate-coverage/README.md in the same change"
        )

    @pytest.mark.parametrize("lane", _coverage_lanes(), ids=str)
    def test_a_lane_that_runs_cargo_states_its_watchdog(
        self, lane: CoverageLane
    ) -> None:
        """A budget nobody chose is one nobody can defend.

        Skipped while no manifest exists, because the watchdog is inert
        then. The test is written now rather than later so the requirement
        arrives with the manifest rather than after the first run it kills.
        """
        if not ROOT_MANIFEST.is_file():
            pytest.skip("no root Cargo.toml, so the cargo watchdog never runs here")
        unstated = [
            index + 1
            for index, watchdog in enumerate(lane.watchdogs)
            if watchdog is None
        ]
        assert not unstated, (
            f"{lane} leaves step(s) {unstated} of {len(lane.watchdogs)} to inherit "
            f"the action's undocumented {WATCHDOG_DEFAULT_SECONDS} s default; set "
            f"{WATCHDOG_VARIABLE} or {WATCHDOG_INPUT} on each from measured runs"
        )

    @pytest.mark.parametrize("lane", _coverage_lanes(), ids=str)
    def test_a_lane_that_runs_cargo_has_a_ceiling_above_its_watchdog(
        self, lane: CoverageLane
    ) -> None:
        """Tier four must not pre-empt tier three.

        The two clocks do not start together: the job timer starts before
        the checkout and the toolchain setup, and the watchdog starts when
        `cargo` does. A ceiling merely equal to the watchdog cancels the job
        before the watchdog can report an overrun, and the cancellation
        discards the log that would have explained it.

        That equality is exactly what this repository's lanes carry today, a
        30 minute ceiling against the 1,800 s default, which is inert only
        because no `cargo` runs. It is asserted here so it cannot survive
        the manifest that would make it real.

        The requirement sums every coverage step's own watchdog rather than
        multiplying one of them, because they need not agree, and it is
        strict by a stated margin, because a ceiling that merely reaches its
        requirement converts a legible overrun into a cancellation with no
        log.
        """
        if not ROOT_MANIFEST.is_file():
            pytest.skip("no root Cargo.toml, so the cargo watchdog never runs here")
        budgets = [
            watchdog if watchdog is not None else WATCHDOG_DEFAULT_SECONDS
            for watchdog in lane.watchdogs
        ]
        required = required_ceiling_seconds(budgets)
        assert lane.ceiling is not None, (
            f"{lane} runs cargo under {len(budgets)} watchdog(s) totalling "
            f"{sum(budgets)} s in a job with no timeout-minutes; the outermost "
            f"tier is missing and GitHub's six-hour default applies"
        )
        assert ceiling_is_sufficient(lane.ceiling, budgets), (
            f"{lane} has a ceiling of {lane.ceiling} minutes, at or below the "
            f"{required / 60:.0f} needed to cover {len(budgets)} watchdog(s) "
            f"totalling {sum(budgets)} s, "
            f"{OUTSIDE_WATCHDOG_ALLOWANCE_SECONDS} s of measured work outside "
            f"them, and a {CEILING_MARGIN_SECONDS} s margin above that sum; an "
            f"overrun would be cancelled rather than reported"
        )
