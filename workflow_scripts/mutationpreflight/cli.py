"""The command line a workflow step invokes, and the run it performs."""

from __future__ import annotations

import dataclasses as dc
import os
import platform
import sys
import typing as typ
from pathlib import Path

from cyclopts import App, Parameter

from .console import emit
from .constants import FAILED, LANES, MUTMUT_PINNED_VERSION
from .declaration import load_declaration
from .models import PhaseResult, Report, Runtimes
from .ports import Run
from .publication import write_report
from .reporting import checked_phases, verdict
from .repository import FileRepository, load_shared_rule
from .resolution import resolution_problem
from .running import run_command

app = App()


@dc.dataclass(frozen=True, slots=True)
class Options:
    """The helper's inputs, read from flags or from the workflow's environment.

    A dataclass rather than five keyword parameters: the options belong to one
    command line, and grouping them keeps the entry point's signature from
    growing with every input a lane learns to accept.

    Attributes
    ----------
    lane : str
        ``mutmut`` or ``cargo``.
    project_python : str
        The interpreter the caller selected for the project.
    helper_python : str
        The interpreter running this helper; the running one by default.
    repository : str
        The checkout to validate.
    output_dir : str
        Where the JSON report and per-check logs are written.
    mutmut_version : str
        The mutmut release the run selects. The sandbox phase judges the
        declaration against that release's copy contract.

    """

    lane: typ.Annotated[str, Parameter(env_var="INPUT_LANE")] = "mutmut"
    project_python: typ.Annotated[str, Parameter(env_var="INPUT_PROJECT_PYTHON")] = ""
    helper_python: typ.Annotated[str, Parameter(env_var="INPUT_HELPER_PYTHON")] = ""
    repository: typ.Annotated[str, Parameter(env_var="INPUT_REPOSITORY")] = "."
    output_dir: typ.Annotated[str, Parameter(env_var="INPUT_OUTPUT_DIR")] = (
        "mutation-preflight"
    )
    mutmut_version: typ.Annotated[str, Parameter(env_var="INPUT_MUTMUT_VERSION")] = (
        MUTMUT_PINNED_VERSION
    )


#: The defaults cyclopts falls back to when neither a flag nor an INPUT_*
#: variable is set. A module-level singleton rather than a call in the
#: signature, which a default argument should never carry.
_DEFAULT_OPTIONS: typ.Final[Options] = Options()


def require_lane(lane: str) -> None:
    """Refuse a lane this helper cannot check, before it reads anything.

    Parameters
    ----------
    lane : str
        The lane the caller asked for. Only the values in ``LANES`` —
        ``mutmut`` and ``cargo`` — name checks this helper can run.

    Raises
    ------
    SystemExit
        Exits 2 for a lane outside the two the helper knows, so a misspelled
        input cannot borrow another lane's checks.

    """
    if lane in LANES:
        return
    emit("mutation_preflight_result", FAILED)
    emit("mutation_preflight_error", f"unknown lane {lane!r}", stream=sys.stderr)
    raise SystemExit(2)


def require_root(root: Path) -> None:
    """Refuse a checkout the phases cannot read, before any of them runs.

    Parameters
    ----------
    root : Path
        The resolved ``repository`` input.

    Raises
    ------
    SystemExit
        Exits 2 when *root* is not a directory. Every phase reads it, and a
        missing or mistyped path would otherwise be reported as a missing
        declaration and a missing ``uv.lock`` rather than as the usage error it
        is.

    """
    if root.is_dir():
        return
    emit("mutation_preflight_result", FAILED)
    emit(
        "mutation_preflight_error",
        f"the repository {str(root)!r} is not a directory",
        stream=sys.stderr,
    )
    raise SystemExit(2)


def abi3_escape_hatch() -> str:
    """Return the ABI3 compatibility setting, without reading it as proof.

    Read here, at the command line, so no phase below reads the environment.
    The value is recorded for diagnosis only: setting it makes a build skip a
    version check rather than establish support.

    Returns
    -------
    str
        ``PYO3_USE_ABI3_FORWARD_COMPATIBILITY`` as the environment spells it,
        or ``"unset"``.

    """
    return os.environ.get("PYO3_USE_ABI3_FORWARD_COMPATIBILITY", "") or "unset"


def emit_findings(results: typ.Iterable[PhaseResult]) -> None:
    """Publish each finding on standard error, prefixed with its phase.

    Parameters
    ----------
    results : Iterable[PhaseResult]
        The phases whose findings are published. Each finding becomes one
        ``mutation_preflight_finding`` line naming the phase it came from.

    """
    for result in results:
        for finding in result.findings:
            emit(
                "mutation_preflight_finding",
                f"{result.name}: {finding}",
                stream=sys.stderr,
            )


@app.default
def main(
    options: typ.Annotated[Options, Parameter(name="*")] = _DEFAULT_OPTIONS,
) -> None:
    """Validate the mutation lane's environment, then publish the envelope.

    Parameters
    ----------
    options : Options
        The lane, both interpreters, the checkout and the output directory.

    Raises
    ------
    SystemExit
        Exits 2 for an unknown lane or an unreadable checkout, and 1 when any
        phase failed or the diagnostics could not be published, so the
        mutation run never starts on an environment that would have failed it,
        and never passes with nothing to show.

    """
    require_lane(options.lane)
    runtimes = Runtimes(
        helper=options.helper_python or platform.python_version(),
        project=options.project_python,
    )
    root = Path(options.repository).resolve()
    require_root(root)
    # Read here, at the command line, so every phase below receives its
    # environment, its rule and its destinations instead of discovering them.
    rule, rule_problem = load_shared_rule()
    files = FileRepository()
    run = Run(
        root=root,
        runtimes=runtimes,
        output_dir=Path(options.output_dir),
        mutmut_version=options.mutmut_version,
        ambient=dict(os.environ),
        summary_path=os.environ.get("GITHUB_STEP_SUMMARY", ""),
        abi3=abi3_escape_hatch(),
        rule=rule,
        rule_problem=rule_problem,
        files=files,
        resolver=resolution_problem,
        runner=run_command,
    )
    results = checked_phases(run, load_declaration(files, root, options.lane))
    report = Report(
        lane=options.lane,
        runtimes=runtimes,
        results=tuple(results),
        verdict=verdict(results),
    )
    emit_findings(results)
    problems = write_report(
        run.files, report, output_dir=run.output_dir, summary_path=run.summary_path
    )
    if problems:
        # Evidence that cannot be published is a failure of the run, not a
        # warning beside a passing verdict: no one can read what it decided.
        for problem in problems:
            emit(
                "mutation_preflight_finding",
                f"reporting: {problem}",
                stream=sys.stderr,
            )
        emit("mutation_preflight_result", FAILED)
        raise SystemExit(1)
    emit("mutation_preflight_result", report.verdict)
    if report.verdict == FAILED:
        raise SystemExit(1)
