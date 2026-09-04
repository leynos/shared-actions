"""Tests for the sccache server `setup-rust` starts.

sccache reads its cache configuration once, when the server starts, and never
rebinds it. Before this step existed the server started as a side effect of the
first client command, which was the `--zero-stats` in the wrapper export, and
nothing named that as the moment the backend was chosen. A reader looking for
where the cache is bound found no such step.

Starting it explicitly, last of the sccache steps, gives that moment a name, an
outcome and a position the manifest can hold: after the backend selection,
after the cache-service restore, and after whatever the caller exported before
this action ran.
"""

from __future__ import annotations

import os
import subprocess
import typing as typ

import pytest
import yaml
from setup_rust_test_helpers import ACTION_PATH, get_step, requires_bash

if typ.TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    from pathlib import Path

SERVER_STEP = "Start the sccache server"
BACKEND_STEP = "Select the sccache backend"
RESTORE_STEP = "Restore the caller's cache service selection"
WRAPPER_STEP = "Export sccache as the rustc wrapper"

#: Every outcome the start may report, and nothing else.
SERVER_OUTCOMES = frozenset(
    {
        "started",
        "started-stats-not-zeroed",
        "start-failed",
        "caller-set",
        "missing-sccache-path",
    }
)


def _steps() -> list[dict[str, typ.Any]]:
    """Return the composite action's step definitions."""
    return yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))["runs"]["steps"]


def _server_script() -> str:
    """Return the Bash fragment the start step declares."""
    script = get_step(SERVER_STEP).get("run")
    assert isinstance(script, str), "the start step must be a shell fragment"
    return script


@pytest.fixture
def fake_sccache(tmp_path: Path) -> Path:
    """Return a stub sccache that records its arguments."""
    binary = tmp_path / "sccache"
    binary.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$@" >> "$(dirname "$0")/args.log"\n'
        'if [[ "$1" == "--start-server" ]]; then\n'
        '  exit "${FAKE_START_EXIT:-0}"\n'
        "fi\n"
        'exit "${FAKE_SCCACHE_EXIT:-0}"\n',
        encoding="utf-8",
    )
    binary.chmod(0o755)
    return binary


def _run_server(
    *,
    sccache_path: str | None,
    wrapper_state: str = "exported",
    start_exit: int | None = None,
    other_exit: int | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the start fragment under a controlled environment."""
    environment = {**os.environ}
    for name in (
        "RUSTC_WRAPPER",
        "SCCACHE_PATH",
        "FAKE_START_EXIT",
        "FAKE_SCCACHE_EXIT",
    ):
        environment.pop(name, None)
    environment["WRAPPER_STATE"] = wrapper_state
    if sccache_path is not None:
        environment["SCCACHE_PATH"] = sccache_path
        environment["RUSTC_WRAPPER"] = sccache_path
    if start_exit is not None:
        environment["FAKE_START_EXIT"] = str(start_exit)
    if other_exit is not None:
        environment["FAKE_SCCACHE_EXIT"] = str(other_exit)
    return subprocess.run(  # noqa: S603,TID251 - exercise the action fragment.
        [requires_bash(), "-c", _server_script()],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
        timeout=10,
    )


def _reported(completed: subprocess.CompletedProcess[str]) -> str | None:
    """Return the bounded server outcome the fragment reported."""
    prefix = "metric setup-rust.sccache.server="
    reported = [
        line.removeprefix(prefix)
        for line in completed.stdout.splitlines()
        if line.startswith(prefix)
    ]
    assert len(reported) <= 1, f"more than one server metric: {reported}"
    return reported[0] if reported else None


class TestManifest:
    """The position is the reason this step exists."""

    def test_it_reads_the_wrapper_export_outcome(self) -> None:
        """The gate is a step output, so the manifest must actually wire it."""
        step = get_step(SERVER_STEP)

        assert step["env"]["WRAPPER_STATE"] == (
            "${{ steps.rustc-wrapper.outputs.state }}"
        )
        assert get_step(WRAPPER_STEP)["id"] == "rustc-wrapper"

    def test_it_is_a_run_step(self) -> None:
        """Only a `run:` step sees what `GITHUB_ENV` carries."""
        step = get_step(SERVER_STEP)

        assert "uses" not in step
        assert isinstance(step.get("run"), str)

    @pytest.mark.parametrize("earlier", [BACKEND_STEP, RESTORE_STEP, WRAPPER_STEP])
    def test_it_follows_every_export_the_server_must_see(self, earlier: str) -> None:
        """A server started before any of these binds a stale configuration."""
        names = [step.get("name") for step in _steps()]

        assert names.index(earlier) < names.index(SERVER_STEP)

    def test_it_is_the_last_sccache_step(self) -> None:
        """Nothing after it may change what the running server already bound."""
        names = [name for name in (step.get("name") for step in _steps()) if name]
        sccache_steps = [
            name
            for name in names
            if "sccache" in name.lower() or "cache service" in name.lower()
        ]

        assert sccache_steps[-1] == SERVER_STEP

    def test_it_is_gated_on_the_same_conditions(self) -> None:
        """Starting a server on a run with no sccache would fail on a name.

        The whole predicate is compared, not its parts, so a future `||` cannot
        widen it unnoticed.
        """
        assert get_step(SERVER_STEP)["if"] == (
            "${{ inputs.use-sccache == 'true' && github.event_name != 'release' }}"
        )


class TestPinning:
    """Every sccache-action invocation names the version it installs."""

    def test_every_invocation_pins_a_version(self) -> None:
        """Left unset, the action asks the GitHub API for the latest release.

        That is a floating dependency and a network call in the critical path.
        The call timed out on #440 and failed the job with "Unable to locate
        executable file: undefined", a red check with no step log behind it.
        """
        invocations = [
            step
            for step in _steps()
            if str(step.get("uses", "")).startswith("mozilla-actions/sccache-action@")
        ]

        assert invocations, "no sccache-action step found; has it been renamed?"
        for step in invocations:
            version = step.get("with", {}).get("version")
            assert version, f"unpinned sccache-action in step {step.get('name')!r}"
            assert version.startswith("v"), version


class TestBehaviour:
    """Run the shipped fragment."""

    def test_starts_a_server(self, fake_sccache: Path) -> None:
        """The ordinary case: the binding happens here and is reported."""
        completed = _run_server(sccache_path=str(fake_sccache))

        assert completed.returncode == 0, completed.stderr
        recorded = (fake_sccache.parent / "args.log").read_text(encoding="utf-8")
        assert "--start-server" in recorded.split()
        assert _reported(completed) == "started"

    def test_stops_any_server_before_starting_one(self, fake_sccache: Path) -> None:
        """A server already running holds the backend it bound then.

        sccache never rebinds, so reusing one started before the cache-service
        restore would keep exactly the configuration this change exists to
        replace.
        """
        completed = _run_server(sccache_path=str(fake_sccache))

        assert completed.returncode == 0, completed.stderr
        recorded = (fake_sccache.parent / "args.log").read_text(encoding="utf-8")
        arguments = recorded.split()
        assert arguments.index("--stop-server") < arguments.index("--start-server")

    def test_leaves_a_caller_owned_wrapper_alone(self, fake_sccache: Path) -> None:
        """A wrapper the export step did not write means the caller owns it.

        The environment cannot say so: an inherited `RUSTC_WRAPPER` may name
        this very binary, when a caller ran `setup-rust` earlier in the job or
        nested it through `rust-build-release`. Stopping that server would
        discard the statistics of everything compiled so far, so the decision
        rests on what the export step published, not on what the value looks
        like.
        """
        completed = _run_server(
            sccache_path=str(fake_sccache), wrapper_state="caller-set"
        )

        assert completed.returncode == 0, completed.stderr
        assert not (fake_sccache.parent / "args.log").exists()
        assert _reported(completed) == "caller-set"

    def test_leaves_an_inherited_wrapper_naming_this_sccache_alone(
        self, fake_sccache: Path
    ) -> None:
        """The nested case, where the value alone would have said `ours`."""
        _run_server(sccache_path=str(fake_sccache), wrapper_state="caller-set")

        assert not (fake_sccache.parent / "args.log").exists()

    def test_fails_when_sccache_path_is_absent(self) -> None:
        """Continuing would leave the caller compiling uncached and unaware."""
        completed = _run_server(sccache_path=None, wrapper_state="exported")

        assert completed.returncode != 0
        assert "did not export SCCACHE_PATH" in completed.stderr
        assert _reported(completed) == "missing-sccache-path"

    def test_zeroes_the_counters_after_starting(self, fake_sccache: Path) -> None:
        """Belt and braces against a `--start-server` that adopted one.

        A server this step started has zero counters already, but a caller's
        later `--show-stats` must measure their build whichever happened.
        """
        completed = _run_server(sccache_path=str(fake_sccache))

        assert completed.returncode == 0, completed.stderr
        arguments = (fake_sccache.parent / "args.log").read_text().split()
        assert arguments.index("--start-server") < arguments.index("--zero-stats")

    def test_a_failure_to_zero_keeps_the_server(self, fake_sccache: Path) -> None:
        """Losing a baseline is a warning; losing the cache would not be."""
        completed = _run_server(sccache_path=str(fake_sccache), other_exit=1)

        assert completed.returncode == 0, completed.stderr
        assert "could not zero sccache statistics" in completed.stdout
        assert _reported(completed) == "started-stats-not-zeroed"

    def test_fails_when_the_server_will_not_start(self, fake_sccache: Path) -> None:
        """An unstarted server is an uncached build, so it must be loud.

        The old behaviour warned and continued, because what failed then was
        zeroing a counter. What fails here is the cache itself.
        """
        completed = _run_server(sccache_path=str(fake_sccache), start_exit=1)

        assert completed.returncode != 0
        assert "could not start the sccache server" in completed.stderr
        assert _reported(completed) == "start-failed"


class TestOutcomeMetric:
    """Every terminal path reports one bounded outcome."""

    @pytest.mark.parametrize(
        ("wrapper_state", "start_exit", "expected"),
        [
            ("exported", None, "started"),
            ("caller-set", None, "caller-set"),
            ("exported", 1, "start-failed"),
        ],
    )
    def test_each_path_reports_its_own(
        self,
        fake_sccache: Path,
        wrapper_state: str,
        start_exit: int | None,
        expected: str,
    ) -> None:
        """A path that reported nothing would be invisible in the series."""
        completed = _run_server(
            sccache_path=str(fake_sccache),
            wrapper_state=wrapper_state,
            start_exit=start_exit,
        )

        assert _reported(completed) == expected

    def test_the_metric_names_no_path(self, fake_sccache: Path) -> None:
        """A binary path in the metric gives the series a value per runner."""
        completed = _run_server(sccache_path=str(fake_sccache))
        outcome = _reported(completed)

        assert outcome in SERVER_OUTCOMES
        assert str(fake_sccache) not in f"metric setup-rust.sccache.server={outcome}"
