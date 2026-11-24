"""The boundaries a phase reads the outside world through.

A phase receives its runner, its resolver, the repository's files, the shared
interpreter rule and its output destinations from one ``Run``. Nothing below
the command line reads ``os.environ``, resolves a path or writes a report of
its own, so a query phase can be shown to be a query by passing it a runner
that fails if it is called, a resolver that answers with a refusal and a
repository that refuses every question without touching the filesystem. The
protocols are narrow on purpose: each names the capability one phase needs, and
``RepositoryQuery`` combines the two a judgement asks so one object can be
wired for it. The adapter that answers the repository's questions,
and performs the writes a phase asks for, lives in ``repository``.
"""

from __future__ import annotations

import dataclasses as dc
import typing as typ

if typ.TYPE_CHECKING:
    from pathlib import Path

    from .copy_set import CopyContract
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
    resolver : CommandResolver
        How the runner establishes which executable the command names. It is
        carried here rather than imported by the runner, so every command path
        resolves through the same injected seam.

    """

    command: tuple[str, ...]
    cwd: Path
    env: typ.Mapping[str, str]
    timeout: int
    ambient: typ.Mapping[str, str]
    resolver: CommandResolver


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
            An empty iterable when the interpreter is inside the range, which
            is how acceptance is reported. Otherwise the reasons the range does
            not establish: the shared rule returns a single finding when the
            interpreter falls outside the range, whether one bound or several
            reject it, and it also returns a finding when the requirement or
            the interpreter cannot be read.

        """
        ...


@dc.dataclass(frozen=True, slots=True)
class Document:
    """One TOML document a repository holds, parsed or refused.

    Attributes
    ----------
    path : Path
        The file the document was read from.
    document : dict[str, object] | None
        The parsed table, or ``None`` when the file could not be read.
    problem : str
        Why the document could not be read, empty when it could. A present but
        unreadable document is distinguished from an absent one by the
        document's presence in the sequence the repository returns.

    """

    path: Path
    document: dict[str, object] | None
    problem: str


@dc.dataclass(frozen=True, slots=True)
class LocatedPath:
    """What a declared path is in the checkout, as the repository found it.

    Attributes
    ----------
    exists : bool
        Whether the path exists, without following its final link.
    resolved : str
        The path with every link resolved, for naming what it became.
    inside_root : bool
        Whether the resolved path stays inside the checkout.
    parts : tuple[str, ...]
        The resolved path relative to the checkout, empty when it escapes.

    """

    exists: bool
    resolved: str
    inside_root: bool
    parts: tuple[str, ...]


class DocumentReader(typ.Protocol):
    """Reads the documents a judgement parses.

    Every operation is phrased as the question a phase asks, never as a path
    operation: a phase receives a parsed document or the lock's package
    records. An adapter that refuses every question therefore leaves the phase
    with a reportable problem rather than a crash.
    """

    def read_documents(
        self, root: Path, relatives: tuple[Path, ...]
    ) -> tuple[Document, ...]:
        """Return every one of *relatives* present under *root*, parsed in order.

        Parameters
        ----------
        root : Path
            The checkout the documents are read from.
        relatives : tuple[Path, ...]
            The documents to read, relative to *root* and in the order the
            caller wants them. An absent relative yields no entry, while a
            present but unreadable one yields a ``Document`` carrying its
            problem, so absence and refusal are never confused.

        Returns
        -------
        tuple[Document, ...]
            One document per present file, in the order asked for.

        """
        ...

    def read_lock_packages(
        self, root: Path
    ) -> tuple[list[dict[str, object]], str | None]:
        """Return ``uv.lock``'s package records, or why they could not be read.

        Parameters
        ----------
        root : Path
            The checkout whose lock is read.

        Returns
        -------
        tuple[list[dict[str, object]], str | None]
            Every package record the lock declares, and why none could be
            read. Records that are not tables are skipped.

        """
        ...

    def read_mutmut_configuration(
        self, root: Path
    ) -> tuple[dict[str, object] | None, str | None]:
        """Return the mutmut configuration the run will use, or why it is unknown.

        Parameters
        ----------
        root : Path
            The checkout holding ``pyproject.toml`` or ``setup.cfg``.

        Returns
        -------
        tuple[dict[str, object] | None, str | None]
            The ``[tool.mutmut]`` table, else the ``[mutmut]`` section, and why
            neither could be established when none was found.

        """
        ...


class PathLocator(typ.Protocol):
    """Answers where a declared path is in the checkout, without copying it."""

    def locate(
        self, root: Path, declared: str
    ) -> tuple[LocatedPath | None, str | None]:
        """Return what *declared* is under *root*, or why it could not be resolved.

        Parameters
        ----------
        root : Path
            The checkout the declared path is relative to.
        declared : str
            The path as a declaration spells it.

        Returns
        -------
        tuple[LocatedPath | None, str | None]
            The path's existence, resolved form and containment, or why the
            operating system could not resolve it.

        """
        ...

    def is_directory(self, path: Path) -> bool:
        """Return whether *path* is a directory.

        Parameters
        ----------
        path : Path
            The resolved path to test.

        Returns
        -------
        bool
            Whether the path is a directory a command could be started in.

        """
        ...


class SandboxProvider(typ.Protocol):
    """Builds the sandbox a check runs in, and removes it afterwards."""

    def materialize(
        self, root: Path, contract: CopyContract
    ) -> tuple[Path | None, tuple[str, ...]]:
        """Copy *contract* into a fresh directory, returning it or why not.

        Parameters
        ----------
        root : Path
            The checkout the contract describes.
        contract : CopyContract
            The selected release's copy contract.

        Returns
        -------
        tuple[Path | None, tuple[str, ...]]
            The materialized sandbox, or ``None`` and why it could not be
            built.

        """
        ...

    def discard(self, sandbox: Path) -> None:
        """Remove a materialized sandbox once the checks have run.

        Parameters
        ----------
        sandbox : Path
            The directory ``materialize`` returned.

        """
        ...


class EvidenceWriter(typ.Protocol):
    """Writes the evidence a run publishes, and says when it cannot."""

    def write_check_logs(
        self, directory: Path, stem: str, name: str, outcome: Ran
    ) -> str | None:
        """Write one check's bounded streams, returning why they were not written.

        Parameters
        ----------
        directory : Path
            The ``checks/`` directory the logs are written into.
        stem : str
            The filename stem that keeps this check's logs apart.
        name : str
            The check's declared name, used when a log error is reported.
        outcome : Ran
            The bounded streams to write as ``<stem>.stdout.log`` and
            ``<stem>.stderr.log``.

        Returns
        -------
        str | None
            Why the logs could not be written, or ``None`` when they were.

        """
        ...

    def append_summary(self, path: Path, rendered: str) -> str | None:
        """Append the rendered summary, returning why it could not be written.

        Parameters
        ----------
        path : Path
            The job summary file to append to.
        rendered : str
            The Markdown to append.

        Returns
        -------
        str | None
            Why the summary could not be written, or ``None`` when it was.

        """
        ...

    def write_report(self, output_dir: Path, document: dict[str, object]) -> str | None:
        """Write the machine-readable report, returning why it was not written.

        Parameters
        ----------
        output_dir : Path
            The directory the report is written into; it is created when it
            does not exist.
        document : dict[str, object]
            The JSON-serializable report to publish.

        Returns
        -------
        str | None
            Why the report could not be written, or ``None`` when it was.

        """
        ...


class RepositoryQuery(DocumentReader, PathLocator, typ.Protocol):
    """The questions a judgement asks of the checkout, and nothing that writes."""


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
    reader : RepositoryQuery
        How a phase reads the documents it judges and locates declared paths.
    sandbox : SandboxProvider
        How a phase builds and discards the sandbox a check runs in.
    evidence : EvidenceWriter
        How a phase writes the logs, summary and report it produces.
    resolver : CommandResolver
        How a phase establishes that a declared command would run, without
        running it.
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
    reader: RepositoryQuery
    sandbox: SandboxProvider
    evidence: EvidenceWriter
    resolver: CommandResolver
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


class CommandResolver(typ.Protocol):
    """Resolves a declared command against the environment it will run in."""

    def __call__(self, name: str, cwd: Path, environment: typ.Mapping[str, str]) -> str:
        """Return why *name* cannot be resolved, or ``""`` when it can.

        Parameters
        ----------
        name : str
            The command as the declaration spells it.
        cwd : Path
            The directory a relative path is anchored at.
        environment : Mapping[str, str]
            The environment the command will run in; a bare name is looked up
            on its ``PATH``.

        Returns
        -------
        str
            The reason the name does not resolve, or an empty string when it
            does.

        """
        ...
