"""Fixtures that run the mutation preflight as the workflow does."""

from __future__ import annotations

import json
import stat
import typing as typ

import pytest
from plumbum import local

from workflow_scripts import mutation_preflight as preflight
from workflow_scripts.tests.mutation_preflight_support import Outcome

if typ.TYPE_CHECKING:
    from pathlib import Path

#: Every input the command line reads from its environment. Each call clears
#: them first, so a value one call set cannot leak into the next.
_PREFLIGHT_INPUTS: typ.Final[tuple[str, ...]] = (
    "INPUT_HELPER_PYTHON",
    "INPUT_LANE",
    "INPUT_MUTMUT_VERSION",
    "INPUT_OUTPUT_DIR",
    "INPUT_PROJECT_PYTHON",
    "INPUT_REPOSITORY",
)


@pytest.fixture
def run_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> typ.Callable[..., Outcome]:
    """Return a callable that runs the preflight through its INPUT_* contract.

    Returns
    -------
    Callable[..., Outcome]
        A callable taking the checkout's root and any ``INPUT_*`` values the
        call wants, by their input name. It runs the command line in-process
        and returns the exit status, the emitted phase envelopes keyed by
        phase name, the final verdict, and everything printed.

    Notes
    -----
    Each call runs under its own ``monkeypatch.context()`` and clears the
    preflight's inputs first, so a call that omits an input gets its default
    rather than the value an earlier call set. ``GITHUB_STEP_SUMMARY`` is left
    alone, so a test that sets it keeps it.
    """
    # A test that wants the job summary sets it after this fixture has run, so
    # the clear happens here rather than inside the callable.
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    def _run(root: Path, **inputs: str) -> Outcome:
        """Run the preflight against *root* with the given INPUT_* values."""
        with monkeypatch.context() as patch:
            for key in _PREFLIGHT_INPUTS:
                patch.delenv(key, raising=False)
            patch.setenv("INPUT_REPOSITORY", str(root))
            patch.setenv("INPUT_OUTPUT_DIR", str(tmp_path / "out"))
            # The helper floor is a property of the helper, not of the host, so
            # a run that does not name one still selects an interpreter the
            # preflight accepts. A caller's own helper_python layers over it.
            patch.setenv("INPUT_HELPER_PYTHON", "3.13")
            for key, value in inputs.items():
                patch.setenv(f"INPUT_{key.upper()}", value)
            capsys.readouterr()
            exit_code = 0
            try:
                preflight.app([])
            except SystemExit as error:
                exit_code = int(error.code or 0)
        captured = capsys.readouterr()
        phases: dict[str, dict[str, object]] = {}
        result = ""
        for line in captured.out.splitlines():
            key, _, value = line.partition("=")
            if key == "mutation_preflight_phase":
                phase = typ.cast("dict[str, object]", json.loads(value))
                phases[str(phase["name"])] = phase
            if key == "mutation_preflight_result":
                result = value
        return Outcome(exit_code, phases, result, captured.out, captured.err)

    return _run


@pytest.fixture
def install_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> typ.Callable[..., None]:
    """Return a callable that installs a fake POSIX command on ``PATH``.

    Returns
    -------
    Callable[..., None]
        A callable taking a command name and the shell body it should run. It
        writes an executable ``#!/bin/sh`` shim under a directory prepended to
        ``PATH``, both for the process environment and for plumbum's own
        lookup, so a declared command resolves to the fake rather than a real
        tool that happens to be installed.

    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", f"{bin_dir}:{local.env['PATH']}")
    monkeypatch.setitem(local.env, "PATH", f"{bin_dir}:{local.env['PATH']}")

    def _install(name: str, body: str) -> None:
        """Write an executable shim named *name* that runs *body*."""
        script = bin_dir / name
        script.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)

    return _install


@pytest.fixture
def empty_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Restrict ``PATH`` to an empty directory, so no real tool can answer.

    The restriction is applied to the process environment and to plumbum's
    lookup, which otherwise answer from different sources. It lasts for the
    test that requests it, so a later test sees the real ``PATH`` again.
    """
    bin_dir = tmp_path / "empty-bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setitem(local.env, "PATH", str(bin_dir))
