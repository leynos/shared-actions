"""The boundaries a phase reads the outside world through.

A phase receives its runner, its environment, the shared interpreter rule and
its output destinations from one ``Run``. Nothing below the command line reads
``os.environ`` or writes a report of its own, so a query phase can be shown to
be a query by passing it a runner that fails if it is called.
"""

from __future__ import annotations

import dataclasses as dc
import typing as typ

if typ.TYPE_CHECKING:
    from pathlib import Path

    from .models import Ran, Runtimes


@dc.dataclass(frozen=True, slots=True)
class Invocation:
    """One command, and the boundary it runs in.

    The four things that decide what a command observes are kept together, so
    a caller cannot pass a working directory without the environment that was
    meant to go with it, and a runner receives one request rather than five
    loose arguments.

    Attributes
    ----------
    command : tuple[str, ...]
        The command and its arguments.
    cwd : Path
        The working directory the command is given.
    env : Mapping[str, str]
        Entries layered over *ambient*.
    timeout : int
        Ceiling on the command's wall clock, in seconds.
    ambient : Mapping[str, str]
        The environment the helper was started with. It is supplied rather
        than read by the runner, so the environment is visible at the call
        site.

    """

    command: tuple[str, ...]
    cwd: Path
    env: typ.Mapping[str, str]
    timeout: int
    ambient: typ.Mapping[str, str]


class Runner(typ.Protocol):
    """Runs one invocation with a bounded result."""

    def __call__(self, invocation: Invocation) -> tuple[Ran | None, str]:
        """Return the invocation's bounded result, or why it did not run.

        Parameters
        ----------
        invocation : Invocation
            The command, the working directory and environment it runs in, and
            the ceiling on its wall clock.

        Returns
        -------
        tuple[Ran | None, str]
            The bounded result and the reason it was stopped or could not
            produce one. A command that could not start returns ``None`` and
            the reason. A command stopped at its ceiling returns its captured
            streams with ``timed_out`` set and the reason. A command that
            completed returns the result and an empty reason.

        """
        ...


class RequirementRule(typ.Protocol):
    """Judges one PEP 508 range against one interpreter."""

    def __call__(
        self, requirement: object, interpreter: str, *, subject: str
    ) -> typ.Iterable[str]:
        """Return one finding per violation of *requirement* by *interpreter*.

        Parameters
        ----------
        requirement : object
            The PEP 508 range to judge.
        interpreter : str
            The interpreter version the range is judged against.
        subject : str
            What a finding names, such as ``the project's requires-python``.

        Returns
        -------
        Iterable[str]
            One finding per violation, in the order the range declares them.
            An empty iterable means the interpreter is inside the range, so
            acceptance is reported by returning nothing.

        """
        ...


@dc.dataclass(frozen=True, slots=True)
class Run:
    """What one preflight run checks, and where its evidence goes.

    Attributes
    ----------
    root : Path
        The checkout being validated.
    runtimes : Runtimes
        Both interpreters, reported separately.
    output_dir : Path
        Where the JSON report and the check logs are written.
    mutmut_version : str
        The mutmut release the run selects, whose copy contract the sandbox
        and checks phases use.
    ambient : Mapping[str, str]
        The environment the helper was started with. A declared check env
        layers over it.
    summary_path : str
        The job summary the run appends to, or ``""`` when the caller sets
        none. Read once at the command line rather than inside the writer.
    abi3 : str
        ``PYO3_USE_ABI3_FORWARD_COMPATIBILITY`` as the caller set it, recorded
        for diagnosis. It is never read as proof of support.
    rule : RequirementRule | None
        The shared requires-python rule (#565), or ``None`` when it could not
        be loaded.
    rule_problem : str
        Why *rule* is ``None``, empty when it is loaded.
    runner : Runner
        How a phase runs a declared command.

    """

    root: Path
    runtimes: Runtimes
    output_dir: Path
    mutmut_version: str
    ambient: typ.Mapping[str, str]
    summary_path: str
    abi3: str
    rule: RequirementRule | None
    rule_problem: str
    runner: Runner


def project_environment(run: Run) -> dict[str, str]:
    """Return the environment entries that select the project interpreter.

    A probe or check that runs the project must use the interpreter this run
    judged, not whichever one the caller's environment happens to name. uv and
    the tools layered on it select an interpreter from ``UV_PYTHON``, so the
    selection is passed through the run rather than inherited.

    The helper's own runtime is deliberately not named: the helper runs above
    this boundary, and a project entry naming it would only overwrite the
    caller's choice with the same or a less suitable one.

    Parameters
    ----------
    run : Run
        The run whose project interpreter the entry selects.

    Returns
    -------
    dict[str, str]
        ``UV_PYTHON`` when a project interpreter was supplied, else empty. A
        caller's explicit declaration layers over this, so an override keeps
        the last word.

    """
    if not run.runtimes.project:
        return {}
    return {"UV_PYTHON": run.runtimes.project}
