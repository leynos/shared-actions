"""Execute a composite action's Bash fragments outside GitHub Actions.

The runner evaluates the small subset of the GitHub Actions expression syntax
composite manifests use, threads each step's ``$GITHUB_OUTPUT`` into the
environment of later steps, and runs every fragment in its own Bash process.
That keeps a test faithful to the action's real step boundaries instead of
hand-building the environment each fragment expects.

Nothing here knows about a particular action. Each fragment is written to a
file and that file is run, because a runner does the same and the difference is
observable: Bash 3.2, which macOS runners ship, replaces itself with the last
command of a ``bash -c`` string when that command is external, discarding any
``ERR`` trap along with the shell.
"""

from __future__ import annotations

import dataclasses as dc
import os
import re
import shutil
import subprocess
import sys
import typing as typ

import pytest

if typ.TYPE_CHECKING:
    from pathlib import Path

_EXPRESSION = re.compile(r"\$\{\{\s*(?P<body>[^}]+?)\s*\}\}")
_STEP_OUTPUT = re.compile(r"^steps\.(?P<step>[\w-]+)\.outputs\.(?P<name>[\w-]+)$")
_INPUT = re.compile(r"^inputs\.(?P<name>[\w-]+)$")


def require_posix_host() -> None:
    """Skip a module that drives an action's Bash fragments on a Windows host.

    The fragments only ever run on a GitHub runner's Bash. Simulating that from
    Git Bash tests the simulation, not the action: the interpreter and PATH
    conversions Git Bash applies to a colon-separated PATH and to command words
    under directories containing a space defeat the harness before any fragment
    executes.
    """
    if sys.platform.startswith("win"):
        pytest.skip(
            "the Bash fragment harness requires a POSIX shell host",
            allow_module_level=True,
        )


def bash_executable() -> str:
    """Return the Bash interpreter, skipping the test when it is absent."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not found on PATH")
    return bash


def bash_path(path: Path) -> str:
    """Return an existing path in the syntax understood by Bash."""
    return subprocess.run(  # noqa: S603,TID251 - Bash is resolved with shutil.which.
        [bash_executable(), "-c", 'cd -- "$1" && pwd -P', "bash", path.as_posix()],
        capture_output=True,
        check=True,
        text=True,
        timeout=30,
    ).stdout.strip()


def bash_file_path(path: Path) -> str:
    """Return a Bash-compatible path for a file that need not exist yet."""
    return f"{bash_path(path.parent)}/{path.name}"


@dc.dataclass
class ActionContext:
    """Resolve the action expressions a composite manifest references."""

    inputs: dict[str, str]
    runner_os: str
    runner_arch: str
    action_path: str
    runner_temp: str = ""
    step_outputs: dict[str, dict[str, str]] = dc.field(default_factory=dict)
    #: Whether every step so far succeeded, which is what an ``if:`` without a
    #: status function implicitly requires.
    succeeded: bool = True

    def resolve(self, body: str) -> str:
        """Return the value of one action expression."""
        match body:
            case "runner.os":
                return self.runner_os
            case "runner.arch":
                return self.runner_arch
            case "runner.temp":
                return self.runner_temp
            case "github.action_path":
                return self.action_path
            case _ if (match := _INPUT.match(body)) is not None:
                return self.inputs.get(match["name"], "")
            case _ if (match := _STEP_OUTPUT.match(body)) is not None:
                return self.step_outputs.get(match["step"], {}).get(match["name"], "")
            case _:
                message = f"unsupported action expression: {body}"
                raise AssertionError(message)

    def render(self, value: str) -> str:
        """Substitute every action expression in ``value``."""
        return _EXPRESSION.sub(lambda match: self.resolve(match["body"]), value)

    def step_env(self, step: dict[str, object]) -> dict[str, str]:
        """Return the rendered ``env`` mapping declared by ``step``."""
        declared = typ.cast("dict[str, str]", step.get("env") or {})
        return {name: self.render(str(value)) for name, value in declared.items()}

    def evaluate_condition(self, step: dict[str, object]) -> bool:
        """Return whether ``step``'s ``if:`` expression selects it.

        Supports an omitted condition, a leading ``failure()``, and the
        equality comparison the installer manifests use. Anything else is a
        test-harness error rather than a silent skip. A condition without a
        status function carries an implicit ``success()``, as on a runner.
        """
        condition = step.get("if")
        if condition is None:
            return self.succeeded
        if not isinstance(condition, str):
            message = f"step {step.get('name')!r} declares a non-string condition"
            raise TypeError(message)
        body = condition.strip()
        if (match := _EXPRESSION.fullmatch(body)) is not None:
            body = match["body"]
        body, requires_failure = _strip_failure_guard(body)
        if requires_failure == self.succeeded:
            return False
        if not body:
            return True
        left, separator, right = body.partition("==")
        if not separator:
            message = f"unsupported step condition: {condition}"
            raise AssertionError(message)
        return self.resolve(left.strip()) == right.strip().strip("'\"")

    def record(self, step: dict[str, object], output_file: Path) -> None:
        """Record a step's ``$GITHUB_OUTPUT`` lines against its identifier."""
        identifier = step.get("id")
        if not isinstance(identifier, str) or not output_file.exists():
            return
        outputs = self.step_outputs.setdefault(identifier, {})
        outputs.update(
            parse_outputs(output_file.read_text(encoding="utf-8").splitlines()),
        )


def _strip_failure_guard(body: str) -> tuple[str, bool]:
    """Split a leading ``failure()`` guard off a condition body."""
    if not body.startswith("failure()"):
        return body, False
    remainder = body[len("failure()") :].lstrip()
    if remainder.startswith("&&"):
        remainder = remainder[2:].strip()
    return remainder, True


def _opens_heredoc(line: str, name: str, separator: str) -> bool:
    """Return whether ``line`` opens a delimited multiline value.

    A runner reads ``name<<DELIMITER`` as an opener only when the ``<<`` comes
    before any ``=``. A single-line value may legitimately contain ``<<``, and
    treating ``key=a<<b`` as an opener would swallow every following line until
    one happened to equal ``b``.
    """
    if not separator:
        return False
    assignment = line.find("=")
    return assignment < 0 or assignment > len(name)


def parse_outputs(lines: list[str]) -> dict[str, str]:
    """Parse ``$GITHUB_OUTPUT`` lines, including delimited multiline values."""
    outputs: dict[str, str] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        name, separator, value = line.partition("<<")
        if _opens_heredoc(line, name, separator):
            delimiter = value
            index += 1
            collected: list[str] = []
            while index < len(lines) and lines[index] != delimiter:
                collected.append(lines[index])
                index += 1
            outputs[name] = "\n".join(collected)
            index += 1
            continue
        if "=" in line:
            name, _, value = line.partition("=")
            outputs[name] = value
        index += 1
    return outputs


@dc.dataclass(frozen=True)
class FragmentEnvironment:
    """Describe where fragments run and what ambient state they observe."""

    base_env: dict[str, str]
    cwd: Path
    output_dir: Path

    def __post_init__(self) -> None:
        """Create the output directory once, so the query below stays a query."""
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def output_file(self, name: str) -> Path:
        """Return the ``$GITHUB_OUTPUT`` file backing one fragment."""
        return self.output_dir / name


@dc.dataclass(frozen=True)
class StepResult:
    """Record one executed fragment and its process result."""

    name: str
    process: subprocess.CompletedProcess[str]


@dc.dataclass(frozen=True)
class LifecycleResult:
    """Aggregate the fragments executed for one lifecycle run."""

    steps: tuple[StepResult, ...]

    @property
    def returncode(self) -> int:
        """Return the first non-zero exit code, or zero when all succeeded.

        A step guarded by ``failure()`` runs after the failure it reports and
        exits zero itself, so the last exit code is not the one that decided
        the job.
        """
        for step in self.steps:
            if step.process.returncode != 0:
                return step.process.returncode
        return 0

    @property
    def stdout(self) -> str:
        """Return the concatenated standard output of every fragment."""
        return "".join(step.process.stdout for step in self.steps)

    @property
    def stderr(self) -> str:
        """Return the concatenated standard error of every fragment."""
        return "".join(step.process.stderr for step in self.steps)

    def executed(self) -> tuple[str, ...]:
        """Return the names of the fragments that ran."""
        return tuple(step.name for step in self.steps)


def run_step(
    step: dict[str, object],
    context: ActionContext,
    environment: FragmentEnvironment,
    output_name: str,
) -> subprocess.CompletedProcess[str]:
    """Run one fragment and record its outputs against ``context``."""
    script = step["run"]
    if not isinstance(script, str):
        message = f"step {step.get('name')!r} declares no Bash fragment"
        raise TypeError(message)
    output_file = environment.output_file(output_name)
    env = {
        **environment.base_env,
        **context.step_env(step),
        "GITHUB_OUTPUT": bash_file_path(output_file),
    }
    # A runner writes a composite fragment to a file and runs that file. Passing
    # the same text to `bash -c` is not the same shell invocation, so the
    # fragment is written out here too and run the way it will be run.
    script_file = environment.output_dir / f"{output_name}.sh"
    script_file.write_text(script, encoding="utf-8")
    process = subprocess.run(  # noqa: S603,TID251 - exercise the Bash fragment.
        [bash_executable(), bash_file_path(script_file)],
        cwd=environment.cwd,
        env=env,
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    context.record(step, output_file)
    return process


def run_lifecycle(
    steps: list[dict[str, object]],
    context: ActionContext,
    environment: FragmentEnvironment,
) -> LifecycleResult:
    """Run the selected fragments in order and collect what they produced.

    A step whose ``if:`` does not select it is skipped. A step that fails marks
    the run failed but does not end the walk, so a step guarded by
    ``failure()`` still gets to report it. Because an ``if:`` without a status
    function carries an implicit ``success()``, a plain sequence of steps
    nonetheless stops at its first failure: everything after it is skipped.

    Parameters
    ----------
    steps : list[dict[str, object]]
        Run-bearing composite steps, in manifest order. Each must declare a
        ``name`` and a ``run``. A ``uses:`` step has no fragment to execute and
        is the caller's to emulate.
    context : ActionContext
        Resolves the steps' expressions. Mutated as the walk proceeds: each
        step's ``$GITHUB_OUTPUT`` is recorded against its ``id``, and
        ``succeeded`` is cleared by the first failure, which is what the
        remaining conditions read.
    environment : FragmentEnvironment
        Where the fragments run and what ambient state they observe.

    Returns
    -------
    LifecycleResult
        The steps that ran, in the order they ran, each with its process
        result. A step its condition skipped does not appear.

    Examples
    --------
    >>> steps = [
    ...     {"name": "probe", "run": "true"},
    ...     {"name": "install", "run": "false"},
    ...     {"name": "verify", "run": "true"},
    ...     {"name": "report", "if": "${{ failure() }}", "run": "echo failed"},
    ... ]
    >>> result = run_lifecycle(steps, context, environment)
    >>> result.executed()
    ('probe', 'install', 'report')
    >>> result.returncode
    1

    ``verify`` is skipped because its absent condition implies ``success()``,
    and ``report`` runs because it asked for the opposite.
    """
    results: list[StepResult] = []
    for index, step in enumerate(steps):
        if not context.evaluate_condition(step):
            continue
        name = typ.cast("str", step["name"])
        process = run_step(step, context, environment, f"{index:02d}-output")
        results.append(StepResult(name=name, process=process))
        if process.returncode != 0:
            context.succeeded = False
    return LifecycleResult(steps=tuple(results))


def ambient_env() -> dict[str, str]:
    """Return the ambient environment with Bash startup files disabled."""
    return {**os.environ, "BASH_ENV": ""}
