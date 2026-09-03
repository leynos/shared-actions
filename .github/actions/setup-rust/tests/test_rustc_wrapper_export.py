"""Tests for the rustc wrapper `setup-rust` exports for sccache.

`mozilla-actions/sccache-action` installs sccache and exports `SCCACHE_PATH`,
but Cargo only routes compilation through sccache when `RUSTC_WRAPPER` names
it. Without the export the action reports sccache as enabled while a consumer
that does not set the wrapper itself compiles without it, which is how Chutoro
came to record zero compile requests.

The manifest tests hold the step's condition and its position after both
sccache-action steps, because the value it reads is their output. The
behavioural tests run the shipped fragment under Bash.
"""

from __future__ import annotations

import os
import subprocess
import typing as typ

import pytest
from setup_rust_test_helpers import ACTION_PATH, get_step, requires_bash

if typ.TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    from pathlib import Path

EXPORT_STEP = "Export sccache as the rustc wrapper"
SCCACHE_STEPS = ("Run sccache (x86_64 macOS)", "Run sccache")


def _steps() -> list[dict[str, typ.Any]]:
    """Return the composite action's step definitions."""
    import yaml

    return yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))["runs"]["steps"]


def _export_script() -> str:
    """Return the Bash fragment the export step declares."""
    script = get_step(EXPORT_STEP).get("run")
    assert isinstance(script, str), "the export step must be a shell fragment"
    return script


def _run_export(
    tmp_path: Path, *, sccache_path: str | None, wrapper: str | None = None
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run the export fragment and return the process and GITHUB_ENV content."""
    github_env = tmp_path / "github-env"
    github_env.touch()
    environment = {**os.environ, "GITHUB_ENV": str(github_env)}
    environment.pop("RUSTC_WRAPPER", None)
    environment.pop("SCCACHE_PATH", None)
    if sccache_path is not None:
        environment["SCCACHE_PATH"] = sccache_path
    if wrapper is not None:
        environment["RUSTC_WRAPPER"] = wrapper
    completed = subprocess.run(  # noqa: S603,TID251 - exercise the action fragment.
        [requires_bash(), "-c", _export_script()],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
        timeout=10,
    )
    return completed, github_env.read_text(encoding="utf-8")


@pytest.fixture
def fake_sccache(tmp_path: Path) -> Path:
    """Return a stub sccache that records the arguments it was given."""
    binary = tmp_path / "sccache"
    binary.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$@" >> "$(dirname "$0")/args.log"\n',
        encoding="utf-8",
    )
    binary.chmod(0o755)
    return binary


class TestManifest:
    """Hold the wiring the export depends on."""

    def test_the_export_follows_both_sccache_steps(self) -> None:
        """`SCCACHE_PATH` is their output, so it cannot precede either."""
        names = [step.get("name") for step in _steps()]

        for sccache_step in SCCACHE_STEPS:
            assert names.index(sccache_step) < names.index(EXPORT_STEP)

    def test_the_export_is_gated_on_the_same_conditions(self) -> None:
        """Exporting when sccache never ran would name a binary that is absent."""
        condition = get_step(EXPORT_STEP)["if"]

        assert "inputs.use-sccache == 'true'" in condition
        assert "github.event_name != 'release'" in condition


class TestBehaviour:
    """Run the shipped fragment."""

    def test_exports_the_installed_sccache(
        self, tmp_path: Path, fake_sccache: Path
    ) -> None:
        """Cargo routes through sccache only when the wrapper names it."""
        completed, written = _run_export(tmp_path, sccache_path=str(fake_sccache))

        assert completed.returncode == 0, completed.stderr
        assert f"RUSTC_WRAPPER={fake_sccache}" in written

    def test_zeroes_the_statistics(self, tmp_path: Path, fake_sccache: Path) -> None:
        """A caller's later --show-stats must measure only its own build."""
        completed, _written = _run_export(tmp_path, sccache_path=str(fake_sccache))

        assert completed.returncode == 0, completed.stderr
        recorded = (fake_sccache.parent / "args.log").read_text(encoding="utf-8")
        assert "--zero-stats" in recorded.split()

    @pytest.mark.parametrize("wrapper", ["/usr/bin/my-wrapper", ""])
    def test_respects_a_caller_that_set_the_wrapper(
        self, tmp_path: Path, fake_sccache: Path, wrapper: str
    ) -> None:
        """An explicit caller value wins, including a deliberate empty one.

        A caller may wrap rustc for its own reasons, so overriding silently
        would take a decision that is not this action's to take.
        """
        completed, written = _run_export(
            tmp_path, sccache_path=str(fake_sccache), wrapper=wrapper
        )

        assert completed.returncode == 0, completed.stderr
        assert "RUSTC_WRAPPER" not in written
        assert "already set" in completed.stdout

    def test_fails_when_sccache_path_is_absent(self, tmp_path: Path) -> None:
        """Silently skipping would leave the caller compiling uncached.

        That is the failure this change exists to end, so it must be loud.
        """
        completed, written = _run_export(tmp_path, sccache_path=None)

        assert completed.returncode != 0
        assert "did not export SCCACHE_PATH" in completed.stderr
        assert written == ""
