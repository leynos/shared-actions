"""Behavioural contract for the `make doctest` target itself.

`test_doctest_coverage.py` reads the Makefile and the tree; nothing there
runs the target. A recipe that dropped `--doctest-modules`, forgot
`$(DOCTEST_PATHS)`, or swallowed pytest's exit status would pass every one
of those reads while collecting nothing, or while letting a wrong example
through. So this module runs `make -f Makefile doctest` for real, in an
isolated temporary workspace, with the `uv` boundary replaced by a stub.

The stub records the command it was given. In the cases that need a
verdict it then runs the real pytest on the arguments after `pytest`, so a
failing example gives a real non-zero exit rather than a canned one. That
second kind of case is also the issue #484 requirement that a deliberately
wrong example makes the gate fail: the wrong example lives in the
temporary workspace, so there is no repository state to restore.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import typing as typ

import pytest

from . import test_doctest_coverage as coverage

if typ.TYPE_CHECKING:
    from pathlib import Path

#: What the stub does with the arguments the recipe hands it. The `uv run
#: --with ... pytest ...` prefix is the part under test; everything after
#: the first `pytest` word is what pytest itself receives.
_STUB_SOURCE: typ.Final[str] = f"""#!{sys.executable}
import json, os, subprocess, sys

argv = sys.argv[1:]
with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(argv) + "\\n")
if os.environ.get("STUB_RUN") != "1":
    sys.exit(0)
index = argv.index("pytest")
sys.exit(subprocess.call([{sys.executable!r}, "-m", "pytest", *argv[index + 1:]]))
"""

_PASSING_EXAMPLE: typ.Final[str] = (
    '"""Module.\n\n    >>> 1 + 1\n    2\n    """\n'.replace("    ", "")
)
_FAILING_EXAMPLE: typ.Final[str] = _PASSING_EXAMPLE.replace("\n2\n", "\n3\n")


class Workspace(typ.NamedTuple):
    """An isolated directory holding the real Makefile and a stub `uv`."""

    root: Path
    log: Path

    def make(
        self, *arguments: str, run_pytest: bool
    ) -> subprocess.CompletedProcess[str]:
        """Run `make -f Makefile doctest` here with the stub as `UV`."""
        # The child pytest must not inherit the parent's xdist or plugin
        # state, or it would try to join the parent's run.
        inherited = {
            name: value
            for name, value in os.environ.items()
            if not name.startswith(("PYTEST_", "PYTHONPATH"))
        }
        environment = {
            **inherited,
            "STUB_LOG": str(self.log),
            "STUB_RUN": "1" if run_pytest else "0",
        }
        return subprocess.run(  # noqa: S603, TID251 - fixed argv, no shell.
            [  # noqa: S607 - make is on PATH.
                "make",
                "-f",
                "Makefile",
                "doctest",
                f"UV={self.root / 'uv-stub'}",
                *arguments,
            ],
            cwd=self.root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )

    def recorded(self) -> list[list[str]]:
        """Return every command the stub was invoked with."""
        if not self.log.exists():
            return []
        lines = self.log.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines]


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    """Return a temporary workspace with the real Makefile and a stub `uv`."""
    shutil.copy2(coverage.MAKEFILE, tmp_path / "Makefile")
    # The target depends on `.venv`; an existing directory satisfies it, so
    # no environment is built and no network is touched.
    (tmp_path / ".venv").mkdir()
    stub = tmp_path / "uv-stub"
    stub.write_text(_STUB_SOURCE, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    return Workspace(tmp_path, tmp_path / "stub.log")


def test_the_recipe_runs_pytest_over_the_declared_paths(workspace: Workspace) -> None:
    """The recipe hands pytest `--doctest-modules` and every DOCTEST_PATHS word."""
    result = workspace.make(run_pytest=False)

    assert result.returncode == 0, result.stderr
    (command,) = workspace.recorded()
    assert command[0] == "run"
    index = command.index("pytest")
    assert "--doctest-modules" in command[index:]
    declared = coverage._makefile_variable(coverage.DOCTEST_PATHS_VARIABLE)
    assert command[index:][-len(declared) :] == declared


def test_the_paths_come_from_the_variable_not_the_recipe(workspace: Workspace) -> None:
    """Overriding DOCTEST_PATHS changes what pytest is given, and nothing else."""
    result = workspace.make("DOCTEST_PATHS=only_this.py", run_pytest=False)

    assert result.returncode == 0, result.stderr
    (command,) = workspace.recorded()
    assert command[command.index("pytest") :][-1] == "only_this.py"
    assert "bool_utils.py" not in command


def test_a_passing_example_exits_zero(workspace: Workspace) -> None:
    """A module whose example is true passes through the real pytest."""
    (workspace.root / "good.py").write_text(_PASSING_EXAMPLE, encoding="utf-8")

    result = workspace.make("DOCTEST_PATHS=good.py", run_pytest=True)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_a_failing_example_exits_non_zero(workspace: Workspace) -> None:
    """A module with one wrong example stops the gate with a non-zero exit.

    This is the issue #484 requirement that a deliberately false example
    fails `make doctest`. The module lives in the throwaway workspace, so
    nothing in the repository needs restoring afterwards.
    """
    (workspace.root / "bad.py").write_text(_FAILING_EXAMPLE, encoding="utf-8")

    result = workspace.make("DOCTEST_PATHS=bad.py", run_pytest=True)

    assert result.returncode != 0, result.stdout + result.stderr
    assert "1 failed" in result.stdout
