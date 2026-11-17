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


@pytest.fixture
def run_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> typ.Callable[..., Outcome]:
    """Return a callable that runs the preflight through its INPUT_* contract."""
    # A test that wants the job summary sets it after this fixture has run, so
    # the clear happens here rather than inside the callable.
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    def _run(root: Path, **inputs: str) -> Outcome:
        """Run the preflight against *root* with the given INPUT_* values."""
        monkeypatch.setenv("INPUT_REPOSITORY", str(root))
        monkeypatch.setenv("INPUT_OUTPUT_DIR", str(tmp_path / "out"))
        for key, value in inputs.items():
            monkeypatch.setenv(f"INPUT_{key.upper()}", value)
        capsys.readouterr()
        exit_code = 0
        try:
            preflight.app([])
        except SystemExit as error:
            exit_code = int(error.code or 0)
        captured = capsys.readouterr().out
        phases: dict[str, dict[str, object]] = {}
        result = ""
        for line in captured.splitlines():
            key, _, value = line.partition("=")
            if key == "mutation_preflight_phase":
                phase = typ.cast("dict[str, object]", json.loads(value))
                phases[str(phase["name"])] = phase
            if key == "mutation_preflight_result":
                result = value
        return Outcome(exit_code, phases, result, captured)

    return _run


@pytest.fixture
def install_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> typ.Callable[..., None]:
    """Return a callable that installs a fake POSIX command on ``PATH``."""
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
    """Restrict ``PATH`` to an empty directory, so no real tool can answer."""
    bin_dir = tmp_path / "empty-bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setitem(local.env, "PATH", str(bin_dir))
