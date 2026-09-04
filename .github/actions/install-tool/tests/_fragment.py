"""Run a step's shell fragment the way the runner would.

The fragment is written to a file and executed as `bash <file>`, not passed to
`bash -c`. That is how the runner invokes it, and the difference is not
cosmetic: `$0`, how a syntax error is reported, and the behaviour of a `return`
outside a function all differ between the two.

Expressions are resolved only in a step's `env:` block, because no fragment in
this action contains one. That is deliberate on the action's side, and asserted
by the manifest tests, so a fragment can be read as shell rather than as a
template.
"""

from __future__ import annotations

import dataclasses
import os
import re
import shutil
import subprocess
import typing as typ

import pytest

if typ.TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    from pathlib import Path

_EXPRESSION = re.compile(r"\$\{\{\s*(?P<body>[^}]+?)\s*\}\}")


def bash_executable() -> str:
    """Return a usable bash, or skip."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not found on PATH")
    return bash


@dataclasses.dataclass
class Context:
    """Everything a step's `env:` block may refer to."""

    inputs: dict[str, str] = dataclasses.field(default_factory=dict)
    steps: dict[str, dict[str, str]] = dataclasses.field(default_factory=dict)
    runner: dict[str, str] = dataclasses.field(default_factory=dict)
    github: dict[str, str] = dataclasses.field(default_factory=dict)

    def resolve(self, expression: str) -> str:
        """Resolve one `${{ ... }}` body against this context."""
        body = expression.strip()
        if body.startswith("inputs."):
            return self.inputs.get(body[len("inputs.") :], "")
        if body.startswith("runner."):
            return self.runner.get(body[len("runner.") :], "")
        if body.startswith("github."):
            return self.github.get(body[len("github.") :], "")
        if body.startswith("steps."):
            _, step_id, _outputs, name = body.split(".", 3)
            return self.steps.get(step_id, {}).get(name, "")
        message = f"unsupported expression: {expression}"
        raise AssertionError(message)

    def expand(self, value: str) -> str:
        """Replace every expression in a value."""
        return _EXPRESSION.sub(lambda match: self.resolve(match.group("body")), value)


@dataclasses.dataclass
class Result:
    """What running one fragment produced."""

    returncode: int
    stdout: str
    stderr: str
    outputs: dict[str, str]
    env: dict[str, str]
    path_additions: list[str]

    @property
    def metrics(self) -> dict[str, str]:
        """Return the bounded metric lines the fragment emitted."""
        emitted: dict[str, str] = {}
        for line in self.stdout.splitlines():
            if line.startswith("metric "):
                name, _, value = line[len("metric ") :].partition("=")
                emitted[name] = value
        return emitted


def _parse_outputs(raw: str) -> dict[str, str]:
    """Parse a `GITHUB_OUTPUT` file the way the runner does."""
    parsed: dict[str, str] = {}
    lines = raw.splitlines()
    index = 0
    while index < len(lines):
        name, separator, value = lines[index].partition("=")
        if "<<" in name:
            name, _, delimiter = name.partition("<<")
            body: list[str] = []
            index += 1
            while index < len(lines) and lines[index] != delimiter:
                body.append(lines[index])
                index += 1
            parsed[name] = "\n".join(body)
        elif separator:
            parsed[name] = value
        index += 1
    return parsed


def run_step(
    step: dict[str, typ.Any],
    context: Context,
    workdir: Path,
    *,
    extra_env: dict[str, str] | None = None,
) -> Result:
    """Write a step's fragment to a file and run it, as the runner does."""
    script = step.get("run")
    if not isinstance(script, str):
        message = f"step {step.get('name')!r} declares no run fragment"
        raise TypeError(message)

    workdir.mkdir(parents=True, exist_ok=True)
    fragment = workdir / "fragment.sh"
    fragment.write_text(script, encoding="utf-8")

    output_file = workdir / "github-output"
    env_file = workdir / "github-env"
    path_file = workdir / "github-path"
    summary_file = workdir / "github-step-summary"
    for handle in (output_file, env_file, path_file, summary_file):
        handle.touch()

    environment = {
        # An inherited BASH_ENV would run before the fragment and could set
        # anything; the runner does not have one.
        "BASH_ENV": "",
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(workdir / "home"),
        "GITHUB_OUTPUT": str(output_file),
        "GITHUB_ENV": str(env_file),
        "GITHUB_PATH": str(path_file),
        "GITHUB_STEP_SUMMARY": str(summary_file),
    }
    (workdir / "home").mkdir(exist_ok=True)
    for name, value in (step.get("env") or {}).items():
        environment[name] = context.expand(str(value))
    if extra_env:
        environment.update(extra_env)

    completed = subprocess.run(  # noqa: S603,TID251 - exercise the shipped fragment.
        [bash_executable(), str(fragment)],
        capture_output=True,
        check=False,
        cwd=workdir,
        env=environment,
        text=True,
        timeout=120,
    )
    return Result(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        outputs=_parse_outputs(output_file.read_text(encoding="utf-8")),
        env=_parse_outputs(env_file.read_text(encoding="utf-8")),
        path_additions=path_file.read_text(encoding="utf-8").splitlines(),
    )
