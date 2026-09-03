"""Execute the install-whitaker action's Bash fragments outside GitHub Actions.

The runner evaluates the small subset of the GitHub Actions expression syntax
the action uses, threads each step's ``$GITHUB_OUTPUT`` into the environment of
later steps, and runs every fragment in its own Bash process. That keeps the
tests faithful to the composite action's real step boundaries instead of
hand-building the environment each fragment expects.
"""

from __future__ import annotations

import dataclasses as dc
import os
import re
import shutil
import subprocess
import typing as typ

import pytest

if typ.TYPE_CHECKING:
    from pathlib import Path

_EXPRESSION = re.compile(r"\$\{\{\s*(?P<body>[^}]+?)\s*\}\}")
_STEP_OUTPUT = re.compile(r"^steps\.(?P<step>[\w-]+)\.outputs\.(?P<name>[\w-]+)$")
_INPUT = re.compile(r"^inputs\.(?P<name>[\w-]+)$")


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
    """Resolve the action expressions the lifecycle fragments reference."""

    inputs: dict[str, str]
    runner_os: str
    runner_arch: str
    action_path: str
    step_outputs: dict[str, dict[str, str]] = dc.field(default_factory=dict)

    def resolve(self, body: str) -> str:
        """Return the value of one action expression."""
        if body == "runner.os":
            return self.runner_os
        if body == "runner.arch":
            return self.runner_arch
        if body == "github.action_path":
            return self.action_path
        if (match := _INPUT.match(body)) is not None:
            return self.inputs.get(match["name"], "")
        if (match := _STEP_OUTPUT.match(body)) is not None:
            return self.step_outputs.get(match["step"], {}).get(match["name"], "")
        message = f"unsupported action expression: {body}"
        raise AssertionError(message)

    def render(self, value: str) -> str:
        """Substitute every action expression in ``value``."""
        return _EXPRESSION.sub(lambda match: self.resolve(match["body"]), value)

    def step_env(self, step: dict[str, object]) -> dict[str, str]:
        """Return the rendered ``env`` mapping declared by ``step``."""
        declared = typ.cast("dict[str, str]", step.get("env") or {})
        return {name: self.render(str(value)) for name, value in declared.items()}

    def record(self, step: dict[str, object], output_file: Path) -> None:
        """Record a step's ``$GITHUB_OUTPUT`` lines against its identifier."""
        identifier = step.get("id")
        if not isinstance(identifier, str) or not output_file.exists():
            return
        outputs = self.step_outputs.setdefault(identifier, {})
        for line in output_file.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                name, _, value = line.partition("=")
                outputs[name] = value


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
        """Return the exit code of the last fragment that ran."""
        return self.steps[-1].process.returncode if self.steps else 0

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
    *,
    base_env: dict[str, str],
    cwd: Path,
    output_file: Path,
) -> subprocess.CompletedProcess[str]:
    """Run one fragment and record its outputs against ``context``."""
    script = step["run"]
    if not isinstance(script, str):
        message = f"step {step.get('name')!r} declares no Bash fragment"
        raise TypeError(message)
    env = {
        **base_env,
        **context.step_env(step),
        "GITHUB_OUTPUT": bash_file_path(output_file),
    }
    process = subprocess.run(  # noqa: S603,TID251 - exercise the Bash fragment.
        [bash_executable(), "-c", script],
        cwd=cwd,
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
    *,
    base_env: dict[str, str],
    cwd: Path,
    output_dir: Path,
) -> LifecycleResult:
    """Run fragments in order, stopping at the first failing fragment."""
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[StepResult] = []
    for index, step in enumerate(steps):
        name = typ.cast("str", step["name"])
        process = run_step(
            step,
            context,
            base_env=base_env,
            cwd=cwd,
            output_file=output_dir / f"{index:02d}-output",
        )
        results.append(StepResult(name=name, process=process))
        if process.returncode != 0:
            break
    return LifecycleResult(steps=tuple(results))


def ambient_env() -> dict[str, str]:
    """Return the ambient environment with Bash startup files disabled."""
    return {**os.environ, "BASH_ENV": ""}
