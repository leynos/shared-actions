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


class CoverageLane(typ.NamedTuple):
    """One job invoking the coverage action, with its budgets.

    Attributes
    ----------
    workflow : str
        The workflow file's name.
    job : str
        The job's identifier.
    watchdog : int or None
        The budget in force, from the step's environment, the job's, the
        workflow's, or the step's ``cargo-wait-timeout`` input. None
        means the lane would inherit the action's default.
    ceiling : int or None
        The job's ``timeout-minutes``, or None when it declares none.
    """

    workflow: str
    job: str
    watchdog: int | None
    ceiling: int | None

    def __str__(self) -> str:
        """Return a location suitable for a failure message.

        Returns
        -------
        str
            ``workflow:job`` for this lane.
        """
        return f"{self.workflow}:{self.job}"


def _watchdog_of(
    document: dict[str, typ.Any], job: dict[str, typ.Any], step: dict[str, typ.Any]
) -> int | None:
    """Return the watchdog budget in force for one coverage step.

    All three environment levels are read, innermost first, as GitHub
    resolves them, and the action's input last: the variable takes
    precedence over the input, so a lane setting both runs on the
    variable.

    Parameters
    ----------
    document : dict[str, typ.Any]
        The whole workflow document.
    job : dict[str, typ.Any]
        The enclosing job.
    step : dict[str, typ.Any]
        The coverage step.

    Returns
    -------
    int or None
        The budget in seconds, or None when nothing sets one.
    """
    levels = (step.get("env"), job.get("env"), document.get("env"))
    for source in levels:
        if isinstance(source, dict) and source.get(WATCHDOG_VARIABLE) is not None:
            return int(str(source[WATCHDOG_VARIABLE]))
    inputs = step.get("with")
    if isinstance(inputs, dict) and inputs.get(WATCHDOG_INPUT) is not None:
        return int(str(inputs[WATCHDOG_INPUT]))
    return None


def _workflow_documents() -> dict[str, dict[str, typ.Any]]:
    """Return every workflow document, keyed by file name.

    Both workflow extensions are read. A lane in the other one would
    otherwise escape every assertion here without failing anything.

    Returns
    -------
    dict[str, dict[str, typ.Any]]
        File name to parsed document.
    """
    documents: dict[str, dict[str, typ.Any]] = {}
    for pattern in ("*.yml", "*.yaml"):
        for path in sorted(WORKFLOWS_DIRECTORY.glob(pattern)):
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
            if isinstance(document, dict):
                documents[path.name] = document
    return documents


def _declared_jobs() -> list[tuple[str, dict[str, typ.Any], str, dict[str, typ.Any]]]:
    """Return every job in every workflow, with its file and document.

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
        for workflow, document in _workflow_documents().items()
        for name, job in (document.get("jobs") or {}).items()
        if isinstance(job, dict)
    ]


def _coverage_steps(job: dict[str, typ.Any]) -> list[dict[str, typ.Any]]:
    """Return the steps in one job that invoke the coverage action.

    Parameters
    ----------
    job : dict[str, typ.Any]
        The parsed job.

    Returns
    -------
    list[dict[str, typ.Any]]
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
    document: dict[str, typ.Any],
    job_name: str,
    job: dict[str, typ.Any],
) -> CoverageLane | None:
    """Return one job's lane, or None when it runs no coverage step.

    Parameters
    ----------
    workflow : str
        The workflow file's name.
    document : dict[str, typ.Any]
        The enclosing document, read for a workflow-level watchdog.
    job_name : str
        The job's identifier.
    job : dict[str, typ.Any]
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
        watchdog=_watchdog_of(document, job, steps[0]),
        ceiling=None if raw is None else int(str(raw)),
    )


def _coverage_lanes() -> tuple[CoverageLane, ...]:
    """Return every job invoking the coverage action, with its budgets.

    Both workflow extensions are read. A lane in the other one would
    otherwise escape every assertion here without failing anything.

    Returns
    -------
    tuple[CoverageLane, ...]
        One entry per coverage-invoking job.
    """
    return tuple(
        lane
        for workflow, document, job_name, job in _declared_jobs()
        if (lane := _coverage_lane(workflow, document, job_name, job)) is not None
    )


def test_this_repository_invokes_its_own_coverage_action() -> None:
    """The contract needs a lane to assert against.

    A rename or a move that stopped the coordinate matching would
    otherwise turn every assertion below into a vacuous pass over an
    empty list, and the loss would look exactly like success.
    """
    assert _coverage_lanes(), (
        f"no workflow job uses {COVERAGE_ACTION_SUFFIX}; either coverage moved "
        f"or this contract stopped recognizing it"
    )


def test_the_cargo_tiers_do_not_apply_until_a_manifest_appears() -> None:
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
def test_a_lane_that_runs_cargo_states_its_watchdog(lane: CoverageLane) -> None:
    """A budget nobody chose is one nobody can defend.

    Skipped while no manifest exists, because the watchdog is inert
    then. The test is written now rather than later so the requirement
    arrives with the manifest rather than after the first run it kills.
    """
    if not ROOT_MANIFEST.is_file():
        pytest.skip("no root Cargo.toml, so the cargo watchdog never runs here")
    assert lane.watchdog is not None, (
        f"{lane} would inherit the action's undocumented "
        f"{WATCHDOG_DEFAULT_SECONDS} s default; set {WATCHDOG_VARIABLE} or "
        f"{WATCHDOG_INPUT} from measured runs"
    )


@pytest.mark.parametrize("lane", _coverage_lanes(), ids=str)
def test_a_lane_that_runs_cargo_has_a_ceiling_above_its_watchdog(
    lane: CoverageLane,
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
    """
    if not ROOT_MANIFEST.is_file():
        pytest.skip("no root Cargo.toml, so the cargo watchdog never runs here")
    watchdog = lane.watchdog or WATCHDOG_DEFAULT_SECONDS
    required = watchdog + OUTSIDE_WATCHDOG_ALLOWANCE_SECONDS
    assert lane.ceiling is not None, (
        f"{lane} runs cargo under a {watchdog} s watchdog in a job with no "
        f"timeout-minutes; the outermost tier is missing and GitHub's "
        f"six-hour default applies"
    )
    assert lane.ceiling * 60 >= required, (
        f"{lane} has a ceiling of {lane.ceiling} minutes, below the "
        f"{required / 60:.0f} needed to cover a {watchdog} s watchdog plus "
        f"{OUTSIDE_WATCHDOG_ALLOWANCE_SECONDS} s of measured work outside it; "
        f"an overrun would be cancelled rather than reported"
    )
