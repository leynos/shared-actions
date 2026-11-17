#!/usr/bin/env -S uv run python
# /// script
# requires-python = ">=3.13"
# dependencies = ["cyclopts>=3.24,<4.0", "packaging>=24,<27", "plumbum>=1.8,<3.0"]
# ///
"""Validate a mutation lane's environment before it enumerates any mutant.

Every failure recorded in issue #591 happened before a mutant was exercised.
The copied sandbox lacked a file the suite reads, the selected interpreter fell
outside the project's ``requires-python``, or an external command the tests
shell out to was never installed. Each was repaired one ``also_copy`` entry at
a time, which is why the same class of failure kept returning.

The repair is a declaration the repository owns and this helper checks once:

- ``sandbox_paths`` — paths that must survive into mutmut's ``mutants/`` copy.
- ``require_commands`` — external commands (and optionally versions) the tests
  invoke.
- ``native`` — a locked native dependency whose interpreter support the run
  depends on.
- ``checks`` — bounded commands run with a declared working directory,
  environment and import root.

The declaration lives in ``[tool.mutation-preflight]`` in the caller's
``pyproject.toml``, or in ``.github/mutation-preflight.toml`` for a repository
without one. Declaring it in both places is refused rather than ranked.

Phases, in the order they run
-----------------------------
``manifest``
    The declaration parses, names only keys this helper understands, and asks
    for nothing its lane cannot check.
``interpreter``
    The project interpreter satisfies the project's ``requires-python``,
    judged by the rule cv005-contracts uses for the coverage clause (#565),
    and the helper's own runtime is reported separately and held to its own
    floor.
``native``
    A declared native dependency is the revision ``uv.lock`` pins, and the
    project interpreter is inside the range that revision supports. Support
    that cannot be established stays ``unknown``; it is never assumed.
``sandbox``
    Every declared path exists, stays inside the checkout, is not a build
    output, and is one mutmut will actually copy.
``commands``
    Every required command resolves, and reports the pinned version when one
    is declared.
``checks``
    Each bounded command succeeds from the working directory, environment and
    import root it declares.

Every phase emits one ``mutation_preflight_phase`` line, whether it succeeded,
failed, was skipped as not applicable, or could only be reported as unknown.
The run ends with ``mutation_preflight_result``, and writes a machine-readable
``mutation-preflight.json`` beside bounded logs even when no mutant exists yet.

Environment Variables
---------------------
INPUT_LANE : str, optional
    ``mutmut`` (default) or ``cargo``. Only the mutmut lane materialises a
    filtered sandbox, so ``sandbox_paths`` is refused on the cargo lane.
INPUT_PROJECT_PYTHON : str, optional
    The interpreter the caller selected for the project, such as ``3.13``.
INPUT_HELPER_PYTHON : str, optional
    The interpreter running this helper. Defaults to the running one.
INPUT_REPOSITORY : str, optional
    The checkout to validate. Defaults to the current directory.
INPUT_OUTPUT_DIR : str, optional
    Where the JSON report and bounded logs are written.
GITHUB_STEP_SUMMARY : str
    Path of the job-summary file, appended to when set.

Usage
-----
As a workflow step::

    - run: uv run --script workflow_scripts/mutation_preflight.py
      env:
        INPUT_LANE: mutmut
        INPUT_PROJECT_PYTHON: ${{ inputs.python-version }}
"""

from __future__ import annotations

import dataclasses as dc
import importlib.util
import json
import os
import platform
import re
import sys
import tomllib
import typing as typ
from configparser import ConfigParser, NoOptionError, NoSectionError
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

from cyclopts import App, Parameter
from plumbum import CommandNotFound, ProcessExecutionError, ProcessTimedOut, local

if __package__:
    from .output import emit
else:
    from output import emit  # type: ignore[import-not-found,no-redef]

app = App()

#: A phase succeeded.
SUCCEEDED: typ.Final[str] = "pass"
#: A phase found something that must stop the run.
FAILED: typ.Final[str] = "fail"
#: A phase does not apply to this lane, or nothing was declared for it.
SKIPPED: typ.Final[str] = "skipped"
#: A phase could not decide, and says so rather than guessing.
UNKNOWN: typ.Final[str] = "unknown"

#: The lanes this helper knows. Only ``mutmut`` copies a filtered sandbox.
LANES: typ.Final[frozenset[str]] = frozenset({"mutmut", "cargo"})

#: The helper scripts the workflows run need this interpreter.
HELPER_PYTHON_FLOOR: typ.Final[str] = "3.13"

#: What mutmut 3.6.0 materialises besides ``source_paths`` and ``also_copy``.
#: Read from ``mutmut/configuration.py`` at the tag the workflow pins; bump
#: this list together with ``mutmut-version``.
MUTMUT_DEFAULT_COPIES: typ.Final[tuple[str, ...]] = (
    "tests",
    "test",
    "setup.cfg",
    "pyproject.toml",
)

#: Root-level globs mutmut 3.6.0 adds to its default copy set.
MUTMUT_DEFAULT_GLOBS: typ.Final[tuple[str, ...]] = ("test*.py",)

#: Directories a mutation sandbox must never carry: they are build output, a
#: tool cache or version control, and copying one either wastes the run or
#: hides a requirement the declaration should have named.
EXCLUDED_DIRECTORIES: typ.Final[frozenset[str]] = frozenset(
    {
        ".cache",
        ".git",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".uv-cache",
        ".uv-tools",
        ".venv",
        ".venv-coverage",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "mutants",
        "node_modules",
        "target",
    }
)

#: The keys a declaration may carry. Anything else is a finding, so a typo
#: cannot read as a requirement that is being checked when it is not.
DECLARATION_KEYS: typ.Final[frozenset[str]] = frozenset(
    {"sandbox_paths", "require_commands", "native", "checks"}
)

#: Keys one ``require_commands`` entry may carry.
COMMAND_KEYS: typ.Final[frozenset[str]] = frozenset({"name", "version", "version_args"})

#: Keys the ``native`` table may carry.
NATIVE_KEYS: typ.Final[frozenset[str]] = frozenset(
    {"package", "revision", "supported_python", "probe"}
)

#: Keys one ``checks`` entry may carry.
CHECK_KEYS: typ.Final[frozenset[str]] = frozenset(
    {"name", "command", "cwd", "timeout_seconds", "env"}
)

#: Bytes of one stream kept for a check. Later bytes are dropped and counted,
#: so a noisy command cannot make the diagnostics unreadable.
LOG_BOUND_BYTES: typ.Final[int] = 4096

#: Ceiling on one declared check, so a hung command cannot outlive the job.
MAX_CHECK_TIMEOUT_SECONDS: typ.Final[int] = 900

#: Ceiling on asking an external command for its version.
VERSION_TIMEOUT_SECONDS: typ.Final[int] = 60

#: A dotted version token in a command's output, used for exact comparison.
VERSION_TOKEN: typ.Final[re.Pattern[str]] = re.compile(r"\d+(?:\.\d+)+")

#: The shared interpreter rule (#565), read from this checkout rather than
#: installed: the caller's environment holds the caller's dependencies.
SHARED_RULE: typ.Final[Path] = (
    Path(__file__).resolve().parents[1]
    / "packages"
    / "cv005-contracts"
    / "cv005_contracts"
    / "requires_python.py"
)


class DeclarationError(ValueError):
    """Raised when a declaration cannot be read as the schema it claims."""


@dc.dataclass(frozen=True, slots=True)
class CommandRequirement:
    """One external command a mutation run needs.

    Attributes
    ----------
    name : str
        The command as it is invoked, resolved through ``PATH``.
    version : str
        Version the command must report, matched as a substring of its version
        output. Empty when any version will do.
    version_args : tuple[str, ...]
        Arguments that make the command print its version.

    """

    name: str
    version: str = ""
    version_args: tuple[str, ...] = ("--version",)


@dc.dataclass(frozen=True, slots=True)
class NativeRequirement:
    """A locked native dependency whose interpreter support matters.

    Attributes
    ----------
    package : str
        The distribution name, as ``uv.lock`` records it.
    revision : str
        The revision the lock must pin, matched inside the locked source.
        Empty when the lock's own record is enough.
    supported_python : str
        The PEP 508 range the dependency supports, judged against the project
        interpreter. Empty when support is not established here.
    probe : tuple[str, ...]
        A command that exercises the built extension. Empty when no probe is
        declared.

    """

    package: str
    revision: str = ""
    supported_python: str = ""
    probe: tuple[str, ...] = ()


@dc.dataclass(frozen=True, slots=True)
class Check:
    """One bounded command run from a declared boundary.

    Attributes
    ----------
    name : str
        The name the finding uses.
    command : tuple[str, ...]
        The command and its arguments.
    cwd : str
        Working directory, relative to the checkout.
    timeout_seconds : int
        Ceiling on the command's wall clock.
    env : tuple[tuple[str, str], ...]
        Environment entries layered over the ambient one. A relative
        ``PYTHONPATH`` is how a subprocess fixture declares its import root.

    """

    name: str
    command: tuple[str, ...]
    cwd: str = "."
    timeout_seconds: int = 300
    env: tuple[tuple[str, str], ...] = ()


@dc.dataclass(frozen=True, slots=True)
class Declaration:
    """A repository's mutation environment requirements, as declared.

    Attributes
    ----------
    source : Path
        The file the declaration was read from.
    lane : str
        The lane the declaration was read for.
    sandbox_paths : tuple[str, ...]
        Paths the mutants sandbox must contain.
    commands : tuple[CommandRequirement, ...]
        External commands the run needs.
    native : NativeRequirement | None
        The native dependency whose support must hold, when declared.
    checks : tuple[Check, ...]
        Bounded commands run before the mutation run.

    """

    source: Path
    lane: str
    sandbox_paths: tuple[str, ...] = ()
    commands: tuple[CommandRequirement, ...] = ()
    native: NativeRequirement | None = None
    checks: tuple[Check, ...] = ()


@dc.dataclass(frozen=True, slots=True)
class Loaded:
    """What reading the declaration produced.

    Attributes
    ----------
    source : Path | None
        The file read, or ``None`` when the repository declares nothing.
    declaration : Declaration | None
        The parsed declaration, or ``None`` when it could not be parsed.
    findings : tuple[str, ...]
        Why the declaration was refused.

    """

    source: Path | None
    declaration: Declaration | None
    findings: tuple[str, ...] = ()


@dc.dataclass(frozen=True, slots=True)
class PhaseResult:
    """One phase's outcome.

    Attributes
    ----------
    name : str
        The phase's stable name, used in the envelope and the summary.
    status : str
        ``pass``, ``fail``, ``skipped`` or ``unknown``.
    findings : tuple[str, ...]
        What the phase found, each naming the path, command or boundary.
    detail : dict[str, object]
        Machine-readable evidence, reported but never inferred from.

    """

    name: str
    status: str
    findings: tuple[str, ...] = ()
    detail: dict[str, object] = dc.field(default_factory=dict)


@dc.dataclass(frozen=True, slots=True)
class Ran:
    """One executed command's bounded result.

    Attributes
    ----------
    exit_code : int
        The command's exit status.
    stdout : str
        Its standard output, truncated at ``LOG_BOUND_BYTES``.
    stderr : str
        Its standard error, truncated at ``LOG_BOUND_BYTES``.

    """

    exit_code: int
    stdout: str
    stderr: str


def _outcome(
    name: str,
    blocking: list[str],
    indeterminate: list[str],
    detail: dict[str, object],
) -> PhaseResult:
    """Return a phase result ranked blocking, then unknown, then success."""
    if blocking:
        return PhaseResult(name, FAILED, (*blocking, *indeterminate), detail)
    if indeterminate:
        return PhaseResult(name, UNKNOWN, tuple(indeterminate), detail)
    return PhaseResult(name, SUCCEEDED, (), detail)


# ---------------------------------------------------------------------------
# Reading the declaration
# ---------------------------------------------------------------------------


def _read_toml(path: Path) -> tuple[dict[str, object] | None, str | None]:
    """Return *path* parsed as TOML, or why it could not be read."""
    try:
        parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        message = f"{path} could not be read: {error}"
        return None, message
    return parsed, None


def _table(document: dict[str, object], *keys: str) -> dict[str, object] | None:
    """Return the table at *keys*, or ``None`` when the path is not a table."""
    current: object = document
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current if isinstance(current, dict) else None


def _declaration_table(
    root: Path,
) -> tuple[Path | None, dict[str, object] | None, list[str]]:
    """Return the declaration's file and table, insisting on one declaration."""
    pyproject = root / "pyproject.toml"
    sidecar = root / ".github" / "mutation-preflight.toml"
    pyproject_table: dict[str, object] | None = None
    if pyproject.is_file():
        document, problem = _read_toml(pyproject)
        if problem is not None:
            return pyproject, None, [problem]
        pyproject_table = _table(document or {}, "tool", "mutation-preflight")
    sidecar_table: dict[str, object] | None = None
    if sidecar.is_file():
        document, problem = _read_toml(sidecar)
        if problem is not None:
            return sidecar, None, [problem]
        sidecar_table = document
    if pyproject_table is not None and sidecar_table is not None:
        message = (
            "both pyproject.toml's [tool.mutation-preflight] and "
            ".github/mutation-preflight.toml declare requirements; keep one, "
            "because which one binds the run would otherwise depend on this "
            "helper's precedence"
        )
        return pyproject, None, [message]
    if pyproject_table is not None:
        return pyproject, pyproject_table, []
    if sidecar_table is not None:
        return sidecar, sidecar_table, []
    return None, None, []


def _unknown_keys(
    table: dict[str, object], allowed: frozenset[str], where: str
) -> list[str]:
    """Return a finding for each key *table* carries that cannot be read."""
    return [
        f"{where} declares unrecognised key {key!r}; supported keys are "
        f"{sorted(allowed)}"
        for key in sorted(set(table) - allowed)
    ]


def _refuse(where: str, expected: str) -> typ.NoReturn:
    """Refuse a member that is not the shape it must be."""
    message = f"{where} must be {expected}"
    raise DeclarationError(message)


def _strings(value: object, where: str) -> tuple[str, ...]:
    """Return *value* as a tuple of strings, refusing anything else."""
    if value is None:
        return ()
    valid = isinstance(value, list) and all(isinstance(item, str) for item in value)
    if not valid:
        _refuse(where, "an array of strings")
    return tuple(value)


def _string(table: dict[str, object], key: str, where: str) -> str:
    """Return one optional string member, refusing a non-string."""
    value = table.get(key, "")
    if not isinstance(value, str):
        _refuse(f"{where}.{key}", "a string")
    return value


def _command_requirement(raw: object, index: int) -> CommandRequirement:
    """Return one ``require_commands`` entry, refusing an unexpected shape."""
    where = f"require_commands[{index}]"
    if not isinstance(raw, dict):
        _refuse(where, "a table")
    problems = _unknown_keys(raw, COMMAND_KEYS, where)
    if problems:
        message = problems[0]
        raise DeclarationError(message)
    name = _string(raw, "name", where)
    if not name:
        _refuse(f"{where}.name", "a non-empty string")
    return CommandRequirement(
        name=name,
        version=_string(raw, "version", where),
        version_args=_strings(raw.get("version_args"), f"{where}.version_args")
        or ("--version",),
    )


def _native_requirement(raw: object) -> NativeRequirement:
    """Return the ``native`` table, refusing an unexpected shape."""
    if not isinstance(raw, dict):
        _refuse("native", "a table")
    problems = _unknown_keys(raw, NATIVE_KEYS, "native")
    if problems:
        message = problems[0]
        raise DeclarationError(message)
    package = _string(raw, "package", "native")
    if not package:
        _refuse("native.package", "a non-empty string")
    return NativeRequirement(
        package=package,
        revision=_string(raw, "revision", "native"),
        supported_python=_string(raw, "supported_python", "native"),
        probe=_strings(raw.get("probe"), "native.probe"),
    )


def _check(raw: object, index: int) -> Check:
    """Return one ``checks`` entry, refusing an unexpected shape."""
    where = f"checks[{index}]"
    if not isinstance(raw, dict):
        _refuse(where, "a table")
    problems = _unknown_keys(raw, CHECK_KEYS, where)
    if problems:
        message = problems[0]
        raise DeclarationError(message)
    name = _string(raw, "name", where)
    command = _strings(raw.get("command"), f"{where}.command")
    if not name:
        _refuse(f"{where}.name", "a non-empty string")
    if not command:
        _refuse(f"{where}.command", "a non-empty array of strings")
    timeout = raw.get("timeout_seconds", 300)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        _refuse(f"{where}.timeout_seconds", "a positive integer")
    env = raw.get("env", {})
    if not isinstance(env, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in env.items()
    ):
        _refuse(f"{where}.env", "a table of strings")
    return Check(
        name=name,
        command=command,
        cwd=_string(raw, "cwd", where) or ".",
        timeout_seconds=min(timeout, MAX_CHECK_TIMEOUT_SECONDS),
        env=tuple(sorted(env.items())),
    )


def _declaration(table: dict[str, object], source: Path, lane: str) -> Declaration:
    """Return the parsed declaration, refusing anything it cannot honour."""
    problems = _unknown_keys(table, DECLARATION_KEYS, str(source))
    if problems:
        message = problems[0]
        raise DeclarationError(message)
    raw_commands = table.get("require_commands", [])
    raw_checks = table.get("checks", [])
    if not isinstance(raw_commands, list):
        _refuse("require_commands", "an array of tables")
    if not isinstance(raw_checks, list):
        _refuse("checks", "an array of tables")
    raw_native = table.get("native")
    declaration = Declaration(
        source=source,
        lane=lane,
        sandbox_paths=_strings(table.get("sandbox_paths"), "sandbox_paths"),
        commands=tuple(
            _command_requirement(raw, index) for index, raw in enumerate(raw_commands)
        ),
        native=None if raw_native is None else _native_requirement(raw_native),
        checks=tuple(_check(raw, index) for index, raw in enumerate(raw_checks)),
    )
    if lane != "mutmut" and declaration.sandbox_paths:
        message = (
            "sandbox_paths is a mutmut-lane requirement; cargo-mutants copies "
            "the whole working tree, so declare the tools it needs instead"
        )
        raise DeclarationError(message)
    return declaration


def load_declaration(root: Path, lane: str) -> Loaded:
    """Return the repository's declaration for *lane*, or why it was refused.

    Parameters
    ----------
    root : Path
        The checkout the declaration belongs to.
    lane : str
        ``mutmut`` or ``cargo``.

    Returns
    -------
    Loaded
        The source read, the parsed declaration when it was accepted, and the
        findings that refused it otherwise. A repository declaring nothing
        yields a ``None`` source and no findings: silence is not a failure, it
        is a lane that has not opted in.

    """
    source, table, findings = _declaration_table(root)
    if source is None or table is None:
        return Loaded(source, None, tuple(findings))
    try:
        declaration = _declaration(table, source, lane)
    except DeclarationError as error:
        return Loaded(source, None, (*findings, str(error)))
    return Loaded(source, declaration, tuple(findings))


# ---------------------------------------------------------------------------
# The interpreter phase
# ---------------------------------------------------------------------------


def _load_shared_rule() -> tuple[typ.Callable[..., list[str]] | None, str]:
    """Return the shared interpreter rule (#565), or why it is unavailable.

    It is loaded by path rather than imported as a package: the caller's
    environment holds the caller's dependencies, not this repository's, and
    importing ``cv005_contracts`` would pull its whole contract graph in for
    one pure function.
    """
    if not SHARED_RULE.is_file():
        return None, f"the shared requires-python rule is absent: {SHARED_RULE}"
    spec = importlib.util.spec_from_file_location(
        "_mutation_preflight_requires_python", SHARED_RULE
    )
    if spec is None or spec.loader is None:
        absent = f"the shared requires-python rule could not be loaded: {SHARED_RULE}"
        return None, absent
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except (OSError, ImportError, SyntaxError) as error:
        return None, f"the shared requires-python rule failed to load: {error}"
    rule = getattr(module, "requirement_violations", None)
    if rule is None:
        return None, (
            f"the shared rule at {SHARED_RULE} no longer exposes requirement_violations"
        )
    return rule, ""


def _judge(requirement: object, interpreter: str, *, subject: str) -> list[str]:
    """Return why *interpreter* falls outside *requirement*, using #565's rule."""
    rule, problem = _load_shared_rule()
    if rule is None:
        return [problem]
    return list(rule(requirement, interpreter, subject=subject))


def _project_requirement(repo_root: Path) -> object:
    """Return the project's declared ``requires-python``, or what stopped the read."""
    path = repo_root / "pyproject.toml"
    if not path.is_file():
        return None
    document, problem = _read_toml(path)
    if problem is not None or document is None:
        return problem
    project = document.get("project")
    if not isinstance(project, dict):
        return None
    return project.get("requires-python")


def _helper_floor_violations(helper_python: str) -> list[str]:
    """Return a finding when the helper's own runtime is below its floor."""
    from packaging.version import InvalidVersion, Version

    try:
        running = Version(helper_python)
        floor = Version(HELPER_PYTHON_FLOOR)
    except InvalidVersion as error:
        return [f"the helper interpreter {helper_python!r} is not a version: {error}"]
    if running < floor:
        message = (
            f"the helper interpreter {helper_python!r} is below the helper "
            f"floor {HELPER_PYTHON_FLOOR}; the workflow's helper scripts need it"
        )
        return [message]
    return []


def _phase_interpreter(
    repo_root: Path, project_python: str, helper_python: str
) -> PhaseResult:
    """Judge the project interpreter, reporting both runtimes separately."""
    detail: dict[str, object] = {
        "helper_python": helper_python,
        "project_python": project_python,
        "same_runtime": helper_python == project_python,
    }
    blocking = _helper_floor_violations(helper_python)
    indeterminate: list[str] = []
    if not project_python:
        indeterminate.append(
            "no project interpreter was supplied, so requires-python was not "
            "checked; set the workflow's python-version input"
        )
        return _outcome("interpreter", blocking, indeterminate, detail)
    requirement = _project_requirement(repo_root)
    detail["requires_python"] = requirement
    if isinstance(requirement, str):
        blocking.extend(
            _judge(
                requirement,
                project_python,
                subject="the project's requires-python",
            )
        )
    elif requirement is not None:
        # A pyproject.toml that cannot be read is the finding itself, so this
        # phase cannot judge the project's range and says so rather than
        # passing it.
        indeterminate.append(str(requirement))
    return _outcome("interpreter", blocking, indeterminate, detail)


# ---------------------------------------------------------------------------
# The native-dependency phase
# ---------------------------------------------------------------------------


def _locked_packages(repo_root: Path) -> tuple[list[dict[str, object]], str | None]:
    """Return ``uv.lock``'s package records, or why they could not be read."""
    lock = repo_root / "uv.lock"
    if not lock.is_file():
        return [], "uv.lock is absent, so no locked revision can be established"
    document, problem = _read_toml(lock)
    if problem is not None:
        return [], problem
    packages = (document or {}).get("package", [])
    if not isinstance(packages, list):
        return [], "uv.lock's package entry is not an array"
    return [item for item in packages if isinstance(item, dict)], None


def _find_locked(
    packages: list[dict[str, object]], name: str
) -> dict[str, object] | None:
    """Return the locked record for *name*, ignoring case and separators."""
    wanted = name.lower().replace("-", "_")
    for package in packages:
        found = package.get("name")
        if isinstance(found, str) and found.lower().replace("-", "_") == wanted:
            return package
    return None


def _source_text(package: dict[str, object]) -> str:
    """Return the locked record's source as text, for identity reporting."""
    source = package.get("source", {})
    if isinstance(source, dict):
        return " ".join(f"{key}={value}" for key, value in sorted(source.items()))
    return str(source)


def _abi3_escape_hatch() -> str:
    """Return the ABI3 compatibility setting, without reading it as proof."""
    return os.environ.get("PYO3_USE_ABI3_FORWARD_COMPATIBILITY", "") or "unset"


def _phase_native(
    repo_root: Path, declaration: Declaration, project_python: str
) -> PhaseResult:
    """Check the declared native dependency's revision and interpreter range."""
    native = declaration.native
    if native is None:
        return PhaseResult(
            "native", SKIPPED, (), {"reason": "no native dependency declared"}
        )
    blocking: list[str] = []
    indeterminate: list[str] = []
    identity: dict[str, object] = {"package": native.package}
    packages, problem = _locked_packages(repo_root)
    locked = _find_locked(packages, native.package)
    if problem is not None:
        indeterminate.append(problem)
    elif locked is None:
        blocking.append(
            f"the declared native dependency {native.package!r} is not in uv.lock"
        )
    else:
        source = _source_text(locked)
        identity["locked_version"] = locked.get("version", "")
        identity["locked_source"] = source
        if native.revision and native.revision not in source:
            blocking.append(
                f"uv.lock pins {native.package} at {source!r}, which is not the "
                f"declared revision {native.revision!r}"
            )
    identity["supported_python"] = native.supported_python
    identity["abi3_forward_compatibility"] = _abi3_escape_hatch()
    if native.supported_python and project_python:
        blocking.extend(
            _judge(
                native.supported_python,
                project_python,
                subject=f"{native.package}'s supported-python",
            )
        )
    if not native.supported_python:
        indeterminate.append(
            f"no supported-python range is declared for {native.package}, so "
            f"interpreter compatibility is unknown rather than established"
        )
    if native.probe:
        outcome, probe_problem = _run(
            native.probe,
            cwd=repo_root,
            env={},
            timeout=MAX_CHECK_TIMEOUT_SECONDS,
        )
        identity["probe"] = _probe_detail(native.probe, outcome, probe_problem)
        if outcome is None:
            blocking.append(
                f"the native probe for {native.package} could not run: {probe_problem}"
            )
        elif outcome.exit_code != 0:
            blocking.append(
                f"the native probe for {native.package} failed with exit code "
                f"{outcome.exit_code}: {_last_line(outcome)}"
            )
    return _outcome("native", blocking, indeterminate, identity)


def _probe_detail(
    command: tuple[str, ...], outcome: Ran | None, problem: str
) -> dict[str, object]:
    """Return what a probe reported, bounded, for the envelope."""
    if outcome is None:
        return {"command": list(command), "problem": problem}
    return {
        "command": list(command),
        "exit_code": outcome.exit_code,
        "stderr": outcome.stderr,
        "stdout": outcome.stdout,
    }


# ---------------------------------------------------------------------------
# The sandbox phase
# ---------------------------------------------------------------------------


def _setup_cfg_table(root: Path) -> dict[str, object] | None:
    """Return ``setup.cfg``'s ``[mutmut]`` section, flattened as mutmut reads it."""
    path = root / "setup.cfg"
    if not path.is_file():
        return None
    parser = ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
        section = parser["mutmut"]
    except (OSError, UnicodeDecodeError, NoSectionError, NoOptionError):
        return None
    table: dict[str, object] = {}
    for key in ("source_paths", "also_copy"):
        if key not in section:
            continue
        raw = section[key]
        # mutmut splits a multi-line value on newlines and wraps a single line
        # as one entry, so a comma-separated line is one path here too.
        table[key] = (
            [item for item in raw.split("\n") if item] if "\n" in raw else [raw]
        )
    return table


def _mutmut_table(root: Path) -> tuple[dict[str, object] | None, str | None]:
    """Return the mutmut configuration the run will use, or why it is unknown."""
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        document, problem = _read_toml(pyproject)
        if problem is not None:
            return None, problem
        table = _table(document or {}, "tool", "mutmut")
        if table is not None:
            return table, None
    table = _setup_cfg_table(root)
    if table is None:
        return None, (
            "neither pyproject.toml's [tool.mutmut] nor setup.cfg's [mutmut] "
            "is present, so what mutmut copies cannot be established"
        )
    return table, None


def _mutmut_copy_set(
    root: Path,
) -> tuple[tuple[str, ...] | None, tuple[str, ...]]:
    """Return the paths mutmut's sandbox will contain, or why they are unknown."""
    table, problem = _mutmut_table(root)
    if table is None:
        return None, (problem or "the mutmut configuration could not be read",)
    try:
        source_paths = _strings(table.get("source_paths"), "[tool.mutmut] source_paths")
        also_copy = _strings(table.get("also_copy"), "[tool.mutmut] also_copy")
    except DeclarationError as error:
        return None, (str(error),)
    if not source_paths:
        message = (
            "the mutmut configuration declares no source_paths, so the copy set "
            "cannot be established; mutmut would guess it"
        )
        return None, (message,)
    return (*MUTMUT_DEFAULT_COPIES, *source_paths, *also_copy), ()


def _path_problem(declared: str) -> str | None:
    """Return why *declared* cannot be a sandbox path, or ``None`` when it can."""
    if not declared.strip():
        return "is empty"
    path = PurePosixPath(declared)
    if path.is_absolute():
        return "is absolute, but sandbox paths are relative to the checkout"
    parts = [part for part in path.parts if part not in ("", ".")]
    if ".." in parts:
        return "escapes the checkout"
    for part in parts:
        if part in EXCLUDED_DIRECTORIES:
            return (
                f"is under {part!r}, which is build output, a tool cache or the "
                f"version-control store; the sandbox must not carry it"
            )
    return None


def _covers(entry: str, wanted: str) -> bool:
    """Return whether copying *entry* materialises *wanted*."""
    entry_path = PurePosixPath(entry.rstrip("/"))
    wanted_path = PurePosixPath(wanted.rstrip("/"))
    return entry_path == wanted_path or entry_path in wanted_path.parents


def _copied_by_default_glob(wanted: str) -> bool:
    """Return whether one of mutmut's root-level default globs matches *wanted*."""
    path = PurePosixPath(wanted)
    return path.parent == PurePosixPath(".") and any(
        fnmatch(path.name, pattern) for pattern in MUTMUT_DEFAULT_GLOBS
    )


def _phase_sandbox(repo_root: Path, declaration: Declaration) -> PhaseResult:
    """Compare the declared sandbox requirements with what mutmut will copy."""
    if not declaration.sandbox_paths:
        return PhaseResult(
            "sandbox", SKIPPED, (), {"reason": "no sandbox_paths declared"}
        )
    copied, problems = _mutmut_copy_set(repo_root)
    detail: dict[str, object] = {
        "declared": list(declaration.sandbox_paths),
        "copied": [] if copied is None else sorted(copied),
    }
    if copied is None:
        return PhaseResult("sandbox", UNKNOWN, problems, detail)
    findings: list[str] = []
    missing: list[str] = []
    uncopied: list[str] = []
    for declared in declaration.sandbox_paths:
        problem = _path_problem(declared)
        if problem is not None:
            findings.append(f"sandbox path {declared!r} {problem}")
            continue
        if not (repo_root / declared).exists():
            missing.append(declared)
            findings.append(
                f"sandbox path {declared!r} is not in the checkout, so the "
                f"declaration names a requirement nothing satisfies"
            )
            continue
        if _copied_by_default_glob(declared):
            continue
        if any(_covers(entry, declared) for entry in copied):
            continue
        uncopied.append(declared)
        findings.append(
            f"sandbox path {declared!r} is not materialised in mutants/: add it "
            f"to [tool.mutmut] also_copy, because mutmut copies only "
            f"{list(MUTMUT_DEFAULT_COPIES)} and root {list(MUTMUT_DEFAULT_GLOBS)} "
            f"besides source_paths and also_copy"
        )
    detail["missing_in_checkout"] = missing
    detail["not_copied"] = uncopied
    if findings:
        return PhaseResult("sandbox", FAILED, tuple(findings), detail)
    return PhaseResult("sandbox", SUCCEEDED, (), detail)


# ---------------------------------------------------------------------------
# Running commands
# ---------------------------------------------------------------------------


def _bounded(text: str) -> str:
    """Return at most ``LOG_BOUND_BYTES`` bytes of *text*, marking the rest."""
    encoded = text.encode("utf-8", "replace")
    if len(encoded) <= LOG_BOUND_BYTES:
        return text
    kept = encoded[:LOG_BOUND_BYTES].decode("utf-8", "ignore")
    dropped = len(encoded) - LOG_BOUND_BYTES
    return f"{kept}\n[... {dropped} bytes truncated]"


def _last_line(outcome: Ran) -> str:
    """Return the last non-empty line a command printed, for a one-line finding."""
    for text in (outcome.stderr, outcome.stdout):
        printed = [line.strip() for line in text.splitlines() if line.strip()]
        if printed:
            return printed[-1]
    return "it printed nothing"


def _run(
    command: tuple[str, ...], *, cwd: Path, env: typ.Mapping[str, str], timeout: int
) -> tuple[Ran | None, str]:
    """Run *command* with bounded output, or return why it could not run."""
    try:
        binary = local[command[0]]
    except CommandNotFound:
        return None, f"{command[0]!r} is not on PATH"
    try:
        completed = binary.run(
            list(command[1:]),
            retcode=None,
            timeout=timeout,
            cwd=str(cwd),
            env=dict(env),
        )
    except ProcessTimedOut:
        return None, f"it did not finish within {timeout} seconds"
    except ProcessExecutionError as error:
        return None, f"it could not be started: {error}"
    return (
        Ran(
            exit_code=int(completed[0]),
            stdout=_bounded(str(completed[1])),
            stderr=_bounded(str(completed[2])),
        ),
        "",
    )


def _reports_version(printed: str, wanted: str) -> bool:
    """Return whether *printed* reports *wanted* as a whole version token.

    A substring test would accept ``2.4`` for ``2.41.0``, which is the kind of
    near-miss this phase exists to catch, so the declared version must equal a
    dotted token in the output or be its dotted prefix: ``2.4`` accepts
    ``2.4.0`` and rejects ``2.41.0``.
    """
    for token in VERSION_TOKEN.findall(printed):
        if token == wanted or token.startswith(f"{wanted}."):
            return True
    return False


def _phase_commands(repo_root: Path, declaration: Declaration) -> PhaseResult:
    """Check that every required command resolves, at the declared version."""
    if not declaration.commands:
        return PhaseResult(
            "commands", SKIPPED, (), {"reason": "no require_commands declared"}
        )
    findings: list[str] = []
    seen: dict[str, object] = {}
    for requirement in declaration.commands:
        outcome, problem = _run(
            (requirement.name, *requirement.version_args),
            cwd=repo_root,
            env={},
            timeout=VERSION_TIMEOUT_SECONDS,
        )
        if outcome is None:
            findings.append(
                f"required command {requirement.name!r} {problem}; install it "
                f"before the mutation run, or stop declaring it"
            )
            seen[requirement.name] = problem
            continue
        printed = f"{outcome.stdout}\n{outcome.stderr}"
        seen[requirement.name] = _bounded(printed.strip())
        if requirement.version and not _reports_version(printed, requirement.version):
            findings.append(
                f"required command {requirement.name!r} does not report "
                f"{requirement.version!r}: it printed {_last_line(outcome)!r}"
            )
    if findings:
        return PhaseResult("commands", FAILED, tuple(findings), seen)
    return PhaseResult("commands", SUCCEEDED, (), seen)


# ---------------------------------------------------------------------------
# The checks phase
# ---------------------------------------------------------------------------


def _check_cwd(repo_root: Path, check: Check) -> tuple[Path | None, str]:
    """Return the working directory *check* names, or why it is unusable."""
    problem = _path_problem(check.cwd) if check.cwd != "." else None
    if problem is not None:
        return None, f"checks[{check.name!r}].cwd {problem}"
    directory = (repo_root / check.cwd).resolve()
    if not directory.is_dir():
        return None, (
            f"checks[{check.name!r}].cwd {check.cwd!r} is not a directory in "
            f"the checkout"
        )
    return directory, ""


def _write_logs(directory: Path, name: str, outcome: Ran) -> None:
    """Write one check's bounded streams, so a failure keeps its evidence."""
    stem = "".join(
        character if character.isalnum() or character in "-_" else "-"
        for character in name
    )
    try:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{stem}.stdout.log").write_text(outcome.stdout, encoding="utf-8")
        (directory / f"{stem}.stderr.log").write_text(outcome.stderr, encoding="utf-8")
    except OSError as error:
        emit(
            "mutation_preflight_log_error",
            f"could not write logs for check {name!r}: {error}",
            stream=sys.stderr,
        )


def _phase_checks(
    repo_root: Path, declaration: Declaration, output_dir: Path
) -> PhaseResult:
    """Run each declared check from the boundary it declares."""
    if not declaration.checks:
        return PhaseResult("checks", SKIPPED, (), {"reason": "no checks declared"})
    findings: list[str] = []
    detail: dict[str, object] = {}
    for check in declaration.checks:
        directory, problem = _check_cwd(repo_root, check)
        if directory is None:
            findings.append(problem)
            detail[check.name] = problem
            continue
        outcome, problem = _run(
            check.command,
            cwd=directory,
            env=dict(check.env),
            timeout=check.timeout_seconds,
        )
        if outcome is None:
            findings.append(
                f"check {check.name!r} could not run from {check.cwd!r}: {problem}"
            )
            detail[check.name] = problem
            continue
        _write_logs(output_dir / "checks", check.name, outcome)
        detail[check.name] = {
            "exit_code": outcome.exit_code,
            "cwd": check.cwd,
            "stdout": outcome.stdout,
            "stderr": outcome.stderr,
        }
        if outcome.exit_code != 0:
            findings.append(
                f"check {check.name!r} failed with exit code "
                f"{outcome.exit_code} from {check.cwd!r}: {_last_line(outcome)}"
            )
    if findings:
        return PhaseResult("checks", FAILED, tuple(findings), detail)
    return PhaseResult("checks", SUCCEEDED, (), detail)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _manifest_phase(loaded: Loaded) -> PhaseResult:
    """Return the manifest phase for what reading the declaration produced."""
    if loaded.source is None:
        reason = (
            "the repository declares no mutation environment requirements, so "
            "only the interpreter was checked"
        )
        return PhaseResult("manifest", SKIPPED, (), {"reason": reason})
    detail: dict[str, object] = {"source": str(loaded.source)}
    if loaded.findings:
        return PhaseResult("manifest", FAILED, loaded.findings, detail)
    return PhaseResult("manifest", SUCCEEDED, (), detail)


def _verdict(results: list[PhaseResult]) -> str:
    """Return the run's verdict: any failure fails the run."""
    return FAILED if any(result.status == FAILED for result in results) else SUCCEEDED


def _phase_envelope(result: PhaseResult) -> dict[str, object]:
    """Return one phase's machine-readable envelope entry."""
    return {
        "name": result.name,
        "status": result.status,
        "findings": list(result.findings),
        "detail": result.detail,
    }


def _render_report(
    results: list[PhaseResult], lane: str, helper_python: str, project_python: str
) -> str:
    """Render every phase as job-summary Markdown."""
    lines = [
        "## Mutation environment preflight",
        "",
        f"- **lane:** {lane}",
        f"- **helper interpreter:** {helper_python}",
        f"- **project interpreter:** {project_python or '(not supplied)'}",
        "",
        "| Phase | Result | Findings |",
        "| ----- | ------ | -------- |",
    ]
    for result in results:
        findings = "<br>".join(result.findings).replace("|", "\\|") or "-"
        lines.append(f"| {result.name} | {result.status} | {findings} |")
    lines.append("")
    return "\n".join(lines)


def _write_report(
    output_dir: Path,
    results: list[PhaseResult],
    lane: str,
    helper_python: str,
    project_python: str,
    verdict: str,
) -> None:
    """Write the JSON report and append the Markdown summary, when asked to."""
    report = {
        "lane": lane,
        "helper_python": helper_python,
        "project_python": project_python,
        "result": verdict,
        "phases": [_phase_envelope(result) for result in results],
    }
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "mutation-preflight.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except OSError as error:
        emit("mutation_preflight_report_error", str(error), stream=sys.stderr)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary:
        return
    rendered = _render_report(results, lane, helper_python, project_python)
    try:
        with Path(summary).open("a", encoding="utf-8") as handle:
            handle.write(rendered)
    except OSError as error:
        emit("mutation_preflight_summary_error", str(error), stream=sys.stderr)


def _checked_phases(
    root: Path,
    loaded: Loaded,
    project_python: str,
    helper_python: str,
    output_dir: Path,
) -> list[PhaseResult]:
    """Run every phase, emitting each as it finishes.

    Emitting as each phase completes means a crash in a later phase still
    leaves the earlier verdicts in the log, which is the point of an envelope
    that does not depend on mutation results existing.
    """
    results = [_manifest_phase(loaded)]
    results.append(_phase_interpreter(root, project_python, helper_python))
    if loaded.declaration is not None:
        results.append(_phase_native(root, loaded.declaration, project_python))
        results.append(_phase_sandbox(root, loaded.declaration))
        results.append(_phase_commands(root, loaded.declaration))
        results.append(_phase_checks(root, loaded.declaration, output_dir))
    for result in results:
        emit("mutation_preflight_phase", _phase_envelope(result))
    return results


@app.default
def main(
    *,
    lane: typ.Annotated[str, Parameter(env_var="INPUT_LANE")] = "mutmut",
    project_python: typ.Annotated[str, Parameter(env_var="INPUT_PROJECT_PYTHON")] = "",
    helper_python: typ.Annotated[str, Parameter(env_var="INPUT_HELPER_PYTHON")] = "",
    repository: typ.Annotated[str, Parameter(env_var="INPUT_REPOSITORY")] = ".",
    output_dir: typ.Annotated[str, Parameter(env_var="INPUT_OUTPUT_DIR")] = (
        "mutation-preflight"
    ),
) -> None:
    """Validate the mutation lane's environment, then publish the envelope.

    Parameters
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

    Raises
    ------
    SystemExit
        Exits 1 when any phase failed, so the mutation run never starts on an
        environment that would have failed it.

    """
    if lane not in LANES:
        emit("mutation_preflight_result", FAILED)
        emit("mutation_preflight_error", f"unknown lane {lane!r}", stream=sys.stderr)
        raise SystemExit(2)
    root = Path(repository).resolve()
    running = helper_python or platform.python_version()
    destination = Path(output_dir)
    loaded = load_declaration(root, lane)
    results = _checked_phases(root, loaded, project_python, running, destination)
    verdict = _verdict(results)
    for result in results:
        for finding in result.findings:
            emit(
                "mutation_preflight_finding",
                f"{result.name}: {finding}",
                stream=sys.stderr,
            )
    emit("mutation_preflight_result", verdict)
    _write_report(destination, results, lane, running, project_python, verdict)
    if verdict == FAILED:
        raise SystemExit(1)


if __name__ == "__main__":
    app()
