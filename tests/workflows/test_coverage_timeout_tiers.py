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

The ceiling requirement sums watchdog *windows* rather than coverage
steps, because a step can arm the watchdog more than once: one passing
`doctests: 'true'` runs `cargo llvm-cov nextest` and then an
uninstrumented `cargo test --doc --workspace`, and one also passing
`with-cucumber-rs` with a non-empty `cucumber-rs-features` runs the
cucumber.rs scenarios under coverage in an invocation of their own. The
doctest case was measured upstream and is carried here rather than left
to a reader; see `TestOneStepCanArmTheWatchdogMoreThanOnce`, which
exercises the window count against workflows written for the case.

The canonical rule is "Test timeouts: four tiers, outermost last" in
`docs/users-guide.md`; "The cargo watchdog" in
`.github/actions/generate-coverage/README.md` covers what the action
itself does with the budget.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest

from .workflow_yaml import load_workflow

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

#: The input that moves a coverage step from one `cargo` invocation to
#: two. The watchdog bounds one `cargo` invocation, so a step that asks
#: for the doctest pass arms it twice and the job ceiling has to contain
#: both windows.
DOCTESTS_INPUT: typ.Final[str] = "doctests"

#: The pair of inputs that add a third `cargo` invocation. The action
#: runs the cucumber.rs scenarios under coverage as a follow-up invocation
#: of its own, so a step asking for both passes arms three windows.
WITH_CUCUMBER_RS_INPUT: typ.Final[str] = "with-cucumber-rs"
CUCUMBER_RS_FEATURES_INPUT: typ.Final[str] = "cucumber-rs-features"

#: The spellings `bool_utils.coerce_bool` reads as true, which is the
#: gate the action itself applies to `doctests`. GitHub passes an
#: unquoted YAML `true` through as a string, so the comparison is
#: case-insensitive and on the stripped text rather than on one literal.
_TRUTHY_SPELLINGS: typ.Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})

#: The action's own default, in seconds. Named here so the failure
#: message can say what a lane would inherit rather than only that it
#: inherits something.
WATCHDOG_DEFAULT_SECONDS: typ.Final[int] = 1800

#: Everything in a coverage job that is not inside a watchdog window.
#: Measured from the worst of several runs, and from runs of every
#: conclusion rather than the successful ones alone: a run terminated by
#: a timeout is the strongest evidence an allowance was too small, and
#: excluding it reproduces the failure it recorded.
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

    Three terms. Every watchdog window may legitimately be spent in
    full, so the budgets are summed rather than one of them multiplied
    by their count: they need not agree, and multiplying the first
    understates a job whose second window is given more. The measured
    work outside those windows is added because the job timer covers it
    and the watchdogs do not. The margin is added because a ceiling that
    merely reaches this sum cancels the job at the moment the watchdog
    would have reported the overrun.

    The budgets are window budgets rather than step budgets. One step
    can arm the watchdog more than once, so the caller expands a step
    into its windows before calling this; see :func:`watchdog_windows`.

    Parameters
    ----------
    budgets : cabc.Sequence[int]
        One watchdog budget per window the job arms, in seconds.

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


def watchdog_windows(step: WorkflowStep) -> int:
    """Return how many watchdog windows one coverage step arms.

    One per `cargo` invocation, because the watchdog bounds one
    invocation rather than one step. Every coverage step spawns the
    instrumented run, and two optional follow-ups add one invocation
    each:

    * `doctests: 'true'` runs `cargo llvm-cov nextest` and then an
      uninstrumented `cargo test --doc --workspace`.
    * `with-cucumber-rs` together with a non-empty `cucumber-rs-features`
      runs the cucumber.rs scenarios under coverage, in an invocation of
      its own. Both inputs are needed: the action's
      ``CucumberSelection.requested`` is the enabled flag *and* a
      non-empty feature path, so enabling cucumber without naming
      features runs nothing and arms nothing.

    A step asking for both passes therefore arms three windows, and one
    asking for neither arms one. The action arms the watchdog inside
    ``_run_cargo``, on a deadline taken per call, so each invocation
    gets a full budget of its own and the ceiling has to contain all of
    them.

    The gates are the action's own. ``coerce_bool`` reads `1`, `true`,
    `yes`, and `on`, case-insensitively, after stripping, so a step that
    declines the input, or writes a spelling the action refuses, arms no
    window for it; reading only the literal `"true"` would miss `'on'`
    and `'True'`, and reading any non-empty value as true would invent a
    window for `'false'`. The feature path is tested for emptiness the
    way the action tests it — ``bool(features)`` on the raw string, not
    on a stripped one — so a whitespace-only value still counts, and the
    window it arms is real even though the invocation it names is not.

    Parameters
    ----------
    step : WorkflowStep
        The coverage step, read for the action's inputs.

    Returns
    -------
    int
        One, plus one for each optional invocation the step requests.

    Examples
    --------
    >>> watchdog_windows({"with": {DOCTESTS_INPUT: "true"}})
    2
    >>> watchdog_windows({"with": {DOCTESTS_INPUT: "false"}})
    1
    >>> watchdog_windows({})
    1
    >>> watchdog_windows(
    ...     {
    ...         "with": {
    ...             WITH_CUCUMBER_RS_INPUT: "true",
    ...             CUCUMBER_RS_FEATURES_INPUT: "tests/features",
    ...         }
    ...     }
    ... )
    2
    """
    inputs = step.get("with")
    if not isinstance(inputs, dict):
        return 1
    windows = 1
    if _requests_doctests(inputs):
        windows += 1
    if _requests_cucumber_rs(inputs):
        windows += 1
    return windows


def _requests_doctests(inputs: cabc.Mapping[str, object]) -> bool:
    """Return whether the action will run a doctest pass for this step.

    Parameters
    ----------
    inputs : cabc.Mapping[str, object]
        The step's ``with`` mapping.

    Returns
    -------
    bool
        True when the input reads as true under the action's own gate.
    """
    requested = str(inputs.get(DOCTESTS_INPUT, "")).strip().lower()
    return requested in _TRUTHY_SPELLINGS


def _requests_cucumber_rs(inputs: cabc.Mapping[str, object]) -> bool:
    """Return whether the action will run cucumber.rs under coverage.

    Both halves are required. The enabled flag goes through the same
    truthiness gate as the doctest input; the feature path is read as
    the action reads it, for emptiness rather than for a stripped
    non-empty value, because that is the test
    ``CucumberSelection.requested`` applies.

    Parameters
    ----------
    inputs : cabc.Mapping[str, object]
        The step's ``with`` mapping.

    Returns
    -------
    bool
        True when the step requests a cucumber.rs invocation.
    """
    enabled = str(inputs.get(WITH_CUCUMBER_RS_INPUT, "")).strip().lower()
    if enabled not in _TRUTHY_SPELLINGS:
        return False
    return bool(str(inputs.get(CUCUMBER_RS_FEATURES_INPUT, "")))


def watchdog_windows_of_job(
    document: WorkflowDocument, job: WorkflowJob
) -> tuple[int | None, ...]:
    """Return every watchdog window a job arms, with its budget.

    A step's resolved budget is repeated once per window it arms,
    because both windows carry the same value: the action resolves the
    budget once and applies it to each `cargo` it spawns. Expansion
    happens here rather than in the reading, so the ceiling sum and the
    per-lane assertions agree on how many windows exist.

    Parameters
    ----------
    document : WorkflowDocument
        The enclosing document, read for a workflow-level watchdog.
    job : WorkflowJob
        The parsed job.

    Returns
    -------
    tuple[int or None, ...]
        One entry per window, in step order, None where the step
        inherits the action's default.
    """
    return tuple(
        budget
        for step in _coverage_steps(job)
        for budget in (_watchdog_of(document, job, step),) * watchdog_windows(step)
    )


def ceiling_is_sufficient(
    ceiling_minutes: int | None, budgets: cabc.Sequence[int]
) -> bool:
    """Return whether a job's ceiling clears its requirement.

    Strictly above, not at: a ceiling sitting exactly on the requirement
    is the case the users' guide rejects by name, because the job is
    cancelled at the moment the watchdog would have reported the overrun
    and a cancellation discards the log.

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
        One watchdog budget per watchdog *window* the job arms, in
        seconds. Windows rather than steps, because one step can arm
        more than one; see :func:`watchdog_windows`.

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
        One budget per watchdog *window* the job arms, in step order,
        each resolved from that step's environment, the job's, the
        workflow's, or that step's ``cargo-wait-timeout`` input. None
        means the step would inherit the action's default.

        Windows rather than steps, because the watchdog bounds one
        `cargo` invocation and a step can spawn two: the two entries a
        doctest-enabled step contributes carry the same budget, since
        the action resolves it once for the step, but the ceiling must
        contain both windows.

        A tuple rather than one value because the budgets need not
        agree either: the variable resolves per step, so a job running
        the action twice can raise it for the feature set that builds
        more, and its ceiling has to contain the sum of what it
        actually set rather than a multiple of whichever step was read
        first.
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


def workflow_documents(
    directory: Path = WORKFLOWS_DIRECTORY,
) -> dict[str, WorkflowDocument]:
    """Return every workflow document in *directory*, keyed by file name.

    This is the only function in these tests that touches the filesystem
    or the YAML parser, so an unreadable or unparsable workflow fails
    here rather than inside an assertion about budgets. Nothing below it
    reaches for a directory of its own: every derivation takes parsed
    documents, and a caller that wants the repository's own workflows
    says so by calling this. Both workflow extensions are read: a lane
    in the other one would otherwise escape every assertion here without
    failing anything. Parsing refuses a key declared twice, which PyYAML
    would otherwise resolve silently to the last value.

    Parameters
    ----------
    directory : Path
        Where the workflows live. Defaults to this repository's.

    Returns
    -------
    dict[str, WorkflowDocument]
        File name to parsed document.
    """
    documents: dict[str, WorkflowDocument] = {}
    for pattern in ("*.yml", "*.yaml"):
        for path in sorted(directory.glob(pattern)):
            document = load_workflow(path)
            if isinstance(document, dict):
                documents[path.name] = document
    return documents


#: This repository's own workflows, parsed once. The filesystem read
#: happens here, at module scope, because ``pytest.mark.parametrize``
#: needs the lanes before any test runs; every derivation below takes
#: documents and reads nothing.
THIS_REPOSITORY: typ.Final[dict[str, WorkflowDocument]] = workflow_documents()


def _declared_jobs(
    documents: dict[str, WorkflowDocument],
) -> list[tuple[str, WorkflowDocument, str, WorkflowJob]]:
    """Return every job in every workflow, with its file and document.

    The documents are required rather than defaulted, so this and every
    derivation above it are pure over what they are handed. Whoever
    wants the repository's own workflows reads them with
    :func:`workflow_documents` and says so, which keeps the filesystem
    at one named boundary instead of behind a default.

    The job's identity travels with it rather than being reconstructed
    from an enclosing loop, which is what lets the lane building be a
    single comprehension.

    Parameters
    ----------
    documents : dict[str, WorkflowDocument]
        Parsed workflows keyed by file name.

    Returns
    -------
    list of tuple
        Workflow name, document, job identifier, and job.
    """
    return [
        (workflow, document, str(name), job)
        for workflow, document in documents.items()
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

    The watchdogs are expanded per window rather than per step, so a
    step that arms the watchdog twice contributes two entries carrying
    the same budget. A doctest-enabled coverage step is the case this
    exists for: the ceiling has to contain both `cargo` invocations,
    and a contract counting one window per step would ask only for one.

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
    if not _coverage_steps(job):
        return None
    raw = job.get("timeout-minutes")
    return CoverageLane(
        workflow=workflow,
        job=job_name,
        watchdogs=watchdog_windows_of_job(document, job),
        ceiling=None if raw is None else int(str(raw)),
    )


def _coverage_lanes(
    documents: dict[str, WorkflowDocument],
) -> tuple[CoverageLane, ...]:
    """Return every job invoking the coverage action, with its budgets.

    Parameters
    ----------
    documents : dict[str, WorkflowDocument]
        Parsed workflows keyed by file name.

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
    documents: dict[str, WorkflowDocument],
) -> list[tuple[CoverageLane, str]]:
    """Return each coverage step's ``cargo-manifest`` input, if any.

    Parameters
    ----------
    documents : dict[str, WorkflowDocument]
        Parsed workflows keyed by file name.

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
        assert _coverage_lanes(THIS_REPOSITORY), (
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
            for lane, manifest in _manifest_inputs(THIS_REPOSITORY)
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
            "docs/users-guide.md in the same change"
        )

    @pytest.mark.parametrize("lane", _coverage_lanes(THIS_REPOSITORY), ids=str)
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
            f"{lane} leaves window(s) {unstated} of {len(lane.watchdogs)} to "
            f"inherit the action's undocumented {WATCHDOG_DEFAULT_SECONDS} s "
            f"default; set {WATCHDOG_VARIABLE} or {WATCHDOG_INPUT} on each "
            f"coverage step from measured runs"
        )

    @pytest.mark.parametrize("lane", _coverage_lanes(THIS_REPOSITORY), ids=str)
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

        The requirement sums every watchdog window the job arms rather
        than multiplying one of them, because they need not agree and
        because one step can arm two. A doctest-enabled coverage step is
        that case: it runs `cargo llvm-cov nextest` and an uninstrumented
        `cargo test --doc`, and each is its own window. The comparison is
        strict by a stated margin, because a ceiling that merely reaches
        its requirement converts a legible overrun into a cancellation
        with no log.
        """
        if not ROOT_MANIFEST.is_file():
            pytest.skip("no root Cargo.toml, so the cargo watchdog never runs here")
        budgets = [
            watchdog if watchdog is not None else WATCHDOG_DEFAULT_SECONDS
            for watchdog in lane.watchdogs
        ]
        required = required_ceiling_seconds(budgets)
        assert lane.ceiling is not None, (
            f"{lane} arms cargo under {len(budgets)} watchdog window(s) "
            f"totalling {sum(budgets)} s in a job with no timeout-minutes; the "
            f"outermost tier is missing and GitHub's six-hour default applies"
        )
        assert ceiling_is_sufficient(lane.ceiling, budgets), (
            f"{lane} has a ceiling of {lane.ceiling} minutes, at or below the "
            f"{required / 60:.0f} needed to cover {len(budgets)} watchdog "
            f"window(s) totalling {sum(budgets)} s, "
            f"{OUTSIDE_WATCHDOG_ALLOWANCE_SECONDS} s of measured work outside "
            f"them, and a {CEILING_MARGIN_SECONDS} s margin above that sum; an "
            f"overrun would be cancelled rather than reported"
        )


class TestOneStepCanArmTheWatchdogMoreThanOnce:
    """One step is not one window, and the ceiling must contain them all.

    The watchdog bounds one `cargo` invocation. A step passing
    `doctests: 'true'` makes two of them, and a step also passing
    `with-cucumber-rs` with a non-empty `cucumber-rs-features` makes
    three, so a ceiling sized per step understates the job by a whole
    window per optional invocation. Netsuke measured the doctest case on
    run 34914144521, whose log prints `cargo watchdog budget: 1800.0s`
    once after `cargo llvm-cov nextest` and once after the uninstrumented
    `cargo test --doc`, and the rule is stated in the users' guide under
    "Test timeouts: four tiers, outermost last".

    This repository has no such lane, so the rule is exercised here
    against workflows written for the case. Nothing below reads a file.
    """

    @staticmethod
    def _document(*, ceiling: int | None = None, **inputs: str) -> WorkflowDocument:
        """Return a synthetic workflow with one coverage step.

        Parameters
        ----------
        ceiling : int or None
            The job's ``timeout-minutes``, or None to declare none.
        **inputs : str
            The action inputs the step passes.

        Returns
        -------
        WorkflowDocument
            A document with a single ``build`` job.
        """
        step: dict[str, object] = {
            "uses": f"{COVERAGE_ACTION_SUFFIX}@abc",
            "env": {WATCHDOG_VARIABLE: "1800"},
        }
        if inputs:
            step["with"] = dict(inputs)
        job: dict[str, object] = {"steps": [step]}
        if ceiling is not None:
            job["timeout-minutes"] = ceiling
        return typ.cast("WorkflowDocument", {"jobs": {"build": job}})

    @pytest.mark.parametrize(
        ("inputs", "windows"),
        [
            pytest.param({"doctests": "true"}, 2, id="the-literal"),
            pytest.param({"doctests": "True"}, 2, id="capitalized"),
            pytest.param({"doctests": "on"}, 2, id="doctests-on"),
            pytest.param({"doctests": "yes"}, 2, id="doctests-yes"),
            pytest.param({"doctests": "1"}, 2, id="doctests-one"),
            pytest.param({"doctests": " true "}, 2, id="doctests-padded"),
            pytest.param({"doctests": "false"}, 1, id="declined"),
            pytest.param({}, 1, id="not-passed"),
            pytest.param(
                {
                    WITH_CUCUMBER_RS_INPUT: "true",
                    CUCUMBER_RS_FEATURES_INPUT: "tests/features",
                },
                2,
                id="cucumber",
            ),
            pytest.param(
                {
                    WITH_CUCUMBER_RS_INPUT: "True",
                    CUCUMBER_RS_FEATURES_INPUT: "tests/features",
                },
                2,
                id="cucumber-capitalized",
            ),
            pytest.param(
                {
                    WITH_CUCUMBER_RS_INPUT: "on",
                    CUCUMBER_RS_FEATURES_INPUT: "tests/features",
                },
                2,
                id="cucumber-on",
            ),
            pytest.param(
                {
                    WITH_CUCUMBER_RS_INPUT: "yes",
                    CUCUMBER_RS_FEATURES_INPUT: "tests/features",
                },
                2,
                id="cucumber-yes",
            ),
            pytest.param(
                {
                    WITH_CUCUMBER_RS_INPUT: "1",
                    CUCUMBER_RS_FEATURES_INPUT: "tests/features",
                },
                2,
                id="cucumber-one",
            ),
            pytest.param(
                {
                    WITH_CUCUMBER_RS_INPUT: " true ",
                    CUCUMBER_RS_FEATURES_INPUT: "tests/features",
                },
                2,
                id="cucumber-padded",
            ),
            pytest.param(
                {
                    WITH_CUCUMBER_RS_INPUT: "false",
                    CUCUMBER_RS_FEATURES_INPUT: "tests/features",
                },
                1,
                id="cucumber-declined",
            ),
            pytest.param(
                {WITH_CUCUMBER_RS_INPUT: "true"},
                1,
                id="cucumber-without-features",
            ),
            pytest.param(
                {
                    WITH_CUCUMBER_RS_INPUT: "true",
                    CUCUMBER_RS_FEATURES_INPUT: "",
                },
                1,
                id="cucumber-with-empty-features",
            ),
            pytest.param(
                {
                    WITH_CUCUMBER_RS_INPUT: "true",
                    CUCUMBER_RS_FEATURES_INPUT: "   ",
                },
                2,
                id="cucumber-with-blank-features",
            ),
            pytest.param(
                {
                    "doctests": "true",
                    WITH_CUCUMBER_RS_INPUT: "true",
                    CUCUMBER_RS_FEATURES_INPUT: "tests/features",
                },
                3,
                id="both",
            ),
            pytest.param(
                {
                    "doctests": "false",
                    WITH_CUCUMBER_RS_INPUT: "true",
                    CUCUMBER_RS_FEATURES_INPUT: "tests/features",
                },
                2,
                id="cucumber-without-doctests",
            ),
        ],
    )
    def test_each_cargo_invocation_is_its_own_window(
        self, inputs: dict[str, str], windows: int
    ) -> None:
        """The gates are the action's own readings of its inputs.

        Every spelling of a truthy value must count, because `coerce_bool`
        reads `1`, `true`, `yes` and `on`, case-insensitively and after
        stripping. Reading only the literal `"true"` would understate a
        step written `'on'`, and reading any non-empty value as truthy
        would invent a window for `'false'`, which runs one `cargo` and
        has one.

        The cucumber gate is a conjunction rather than a single input.
        Enabling it without naming features runs nothing, so it arms no
        window; the parametrisation carries both the enabled-and-named
        rows and the enabled-but-featureless ones that must not add a
        window. A blank feature path is *not* one of those: the action
        reads ``bool(features)`` on the raw string, which is true for
        whitespace, so it does arm a window and the row asserts as much.
        """
        (lane,) = _coverage_lanes({"ci.yml": self._document(**inputs)})

        assert len(lane.watchdogs) == windows, (
            f"a coverage step passing {inputs!r} must arm {windows} watchdog "
            f"window(s), got {lane.watchdogs!r}"
        )
        assert len(set(lane.watchdogs)) == 1, (
            f"every window of one step carries the same resolved budget, since "
            f"the action resolves it once; got {lane.watchdogs!r}"
        )

    def test_the_two_windows_raise_the_ceiling_requirement(self) -> None:
        """The second window adds its whole budget to the requirement.

        The two readings differ by exactly one watchdog budget: 3,600 s
        against 1,800 s of window here. 5,400 s is Netsuke's two-window
        figure, from a 900 s outside allowance and a 900 s margin. Those
        two are not this module's constants, which are 600 s and 900 s,
        so a step in this estate would need 5,100 s rather than 5,400 s.
        """
        one_window = _coverage_lanes({"ci.yml": self._document(doctests="false")})[0]
        two_windows = _coverage_lanes({"ci.yml": self._document(doctests="true")})[0]

        assert sum(typ.cast("list[int]", list(one_window.watchdogs))) == 1800, (
            f"a step declining doctests arms one 1,800 s window, got "
            f"{one_window.watchdogs!r}"
        )
        assert sum(typ.cast("list[int]", list(two_windows.watchdogs))) == 3600, (
            f"a doctest-enabled step arms two 1,800 s windows, got "
            f"{two_windows.watchdogs!r}"
        )
        assert required_ceiling_seconds([1800, 1800]) == (
            required_ceiling_seconds([1800]) + 1800
        ), "the second window must raise the requirement by its own budget"
        assert 2 * 1800 + 900 + 900 == 5400, (
            "Netsuke's two-window ceiling arithmetic: 2 x 1,800 s of window, "
            "900 s of measured work outside them and a 900 s margin is 5,400 s, "
            "which is 90 minutes"
        )

    def test_a_third_window_raises_it_again(self) -> None:
        """Both optional invocations together arm three windows, not two.

        The cucumber.rs run is an invocation of its own, so a step asking
        for it *and* for the doctest pass arms three. A model that counts
        one window for cucumber and two for doctests without letting them
        compose would report two and understate the ceiling by a whole
        budget, which is the same defect as counting one per step.
        """
        three = self._document(
            ceiling=120,
            doctests="true",
            **{
                WITH_CUCUMBER_RS_INPUT: "true",
                CUCUMBER_RS_FEATURES_INPUT: "tests/features",
            },
        )
        two = self._document(
            ceiling=120,
            **{
                WITH_CUCUMBER_RS_INPUT: "true",
                CUCUMBER_RS_FEATURES_INPUT: "tests/features",
            },
        )

        (three_lane,) = _coverage_lanes({"ci.yml": three})
        (two_lane,) = _coverage_lanes({"ci.yml": two})

        assert len(three_lane.watchdogs) == 3, (
            f"doctests and cucumber.rs together arm three windows, got "
            f"{three_lane.watchdogs!r}"
        )
        assert len(two_lane.watchdogs) == 2, (
            f"cucumber.rs without doctests arms two windows, got {two_lane.watchdogs!r}"
        )
        assert required_ceiling_seconds([1800, 1800, 1800]) == (
            required_ceiling_seconds([1800, 1800]) + 1800
        ), "the third window must raise the requirement by its own budget"
        assert required_ceiling_seconds([1800, 1800, 1800]) == 6900, (
            "three windows, this repository's ten minutes outside them and its "
            "fifteen-minute margin sum to 6,900 s, which is 115 minutes"
        )

    def test_the_same_step_is_judged_differently_by_window_count(self) -> None:
        """One ceiling can clear one reading and fail the other.

        The two readings differ by a whole window, so the defect this
        guards against is a missing term rather than a wrong magnitude.
        The ceiling is held fixed across both assertions — the same lane's
        ``timeout-minutes`` drives each comparison — because a test that
        moved the ceiling between readings would pass whether or not the
        window count was right.

        At 85 minutes the ceiling sits exactly on the 5,100 s two-window
        requirement, so the strict comparison must reject it while the
        one-window reading, needing only 3,300 s, clears it.
        """
        (lane,) = _coverage_lanes(
            {"ci.yml": self._document(ceiling=85, doctests="true")}
        )
        budgets = [
            watchdog if watchdog is not None else WATCHDOG_DEFAULT_SECONDS
            for watchdog in lane.watchdogs
        ]
        ceiling = typ.cast("int", lane.ceiling)

        assert ceiling_is_sufficient(ceiling, [1800]) is True, (
            "an 85-minute ceiling clears the one-window requirement, which is "
            "why counting one window per step passes the wrong check"
        )
        assert required_ceiling_seconds([1800]) == 3300, (
            "one window plus this repository's ten minutes outside it and its "
            "fifteen-minute margin is 3,300 s"
        )
        assert ceiling_is_sufficient(ceiling, budgets) is False, (
            "the same 85-minute ceiling sits exactly on the 5,100 s "
            "two-window requirement, so the strict comparison must reject it "
            "rather than accept it"
        )
        assert required_ceiling_seconds([1800, 1800]) == 5100, (
            "two windows, this repository's ten minutes outside them and its "
            "fifteen-minute margin sum to 5,100 s"
        )
        assert ceiling_is_sufficient(86, budgets) is True, (
            "one minute above the requirement must pass, so the rejection is "
            "about strictness rather than about magnitude"
        )
