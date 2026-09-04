"""Tests for the sccache backend `setup-rust` selects.

sccache stores compiler output on local disk unless `SCCACHE_GHA_ENABLED` is
set, and nothing persists that directory between jobs, so a consumer that set
only `RUSTC_WRAPPER` paid for the wrapper and got no cache across runs.

Ordering is the subtle part and the manifest tests hold it. sccache binds its
backend once, when the server starts, and the first thing this action does that
starts one is the `--zero-stats` in the wrapper step. `GITHUB_ENV` reaches only
the next step, so the variable has to be written *before* the sccache-action
steps. Exported afterwards, as the wrapper is, it would be read by nobody while
every log line claimed the backend was selected.

The sccache-action steps start no server of their own. Measurement on Ubicloud
established that what they do instead is write `ACTIONS_CACHE_SERVICE_V2=on` to
`GITHUB_ENV`, which is issue #441 and not this ordering.
"""

from __future__ import annotations

import os
import subprocess
import typing as typ

import pytest
import yaml
from hypothesis import given, settings
from hypothesis import strategies as st
from setup_rust_test_helpers import ACTION_PATH, get_step, requires_bash

if typ.TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    from pathlib import Path

BACKEND_STEP = "Select the sccache backend"
SCCACHE_STEPS = ("Run sccache (x86_64 macOS)", "Run sccache")
WRAPPER_STEP = "Export sccache as the rustc wrapper"

#: Every outcome the selection may report, and nothing else.
BACKEND_OUTCOMES = frozenset({"gha", "local", "caller"})


def _steps() -> list[dict[str, typ.Any]]:
    """Return the composite action's step definitions."""
    return yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))["runs"]["steps"]


def _backend_script() -> str:
    """Return the Bash fragment the selection step declares."""
    script = get_step(BACKEND_STEP).get("run")
    assert isinstance(script, str), "the selection step must be a shell fragment"
    return script


def _run_backend(
    tmp_path: Path,
    *,
    gha_enabled: str | None = None,
    sccache_dir: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run the selection fragment and return the process and `GITHUB_ENV`."""
    github_env = tmp_path / "github-env"
    github_env.touch()
    environment = {**os.environ, "GITHUB_ENV": str(github_env)}
    for name in ("SCCACHE_GHA_ENABLED", "SCCACHE_DIR"):
        environment.pop(name, None)
    if gha_enabled is not None:
        environment["SCCACHE_GHA_ENABLED"] = gha_enabled
    if sccache_dir is not None:
        environment["SCCACHE_DIR"] = sccache_dir
    completed = subprocess.run(  # noqa: S603,TID251 - exercise the action fragment.
        [requires_bash(), "-c", _backend_script()],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
        timeout=10,
    )
    return completed, github_env.read_text(encoding="utf-8")


def _reported_backend(completed: subprocess.CompletedProcess[str]) -> str | None:
    """Return the bounded backend outcome the fragment reported."""
    prefix = "metric setup-rust.sccache.backend="
    reported = [
        line.removeprefix(prefix)
        for line in completed.stdout.splitlines()
        if line.startswith(prefix)
    ]
    assert len(reported) <= 1, f"more than one backend metric: {reported}"
    return reported[0] if reported else None


class TestOrdering:
    """The one thing that makes the export effective rather than decorative."""

    def test_the_selection_precedes_every_sccache_step(self) -> None:
        """The server binds its backend at start, inside those steps.

        Exported afterwards the variable would be read by nobody, and the job
        would keep a local-disk cache while the log claimed otherwise.
        """
        names = [step.get("name") for step in _steps()]

        for sccache_step in SCCACHE_STEPS:
            assert names.index(BACKEND_STEP) < names.index(sccache_step)

    def test_the_wrapper_export_still_follows_them(self) -> None:
        """The two exports sit on opposite sides, each for its own reason.

        The wrapper needs `SCCACHE_PATH`, which those steps produce; the
        backend must be chosen before they run. Neither can move.
        """
        names = [step.get("name") for step in _steps()]

        for sccache_step in SCCACHE_STEPS:
            assert names.index(sccache_step) < names.index(WRAPPER_STEP)

    def test_the_selection_is_gated_on_the_same_conditions(self) -> None:
        """Whole predicate, so a future `||` cannot widen it unnoticed."""
        assert get_step(BACKEND_STEP)["if"] == (
            "${{ inputs.use-sccache == 'true' && github.event_name != 'release' }}"
        )


class TestBehaviour:
    """Run the shipped fragment."""

    def test_selects_the_github_actions_backend(self, tmp_path: Path) -> None:
        """The default must be the backend that survives the job."""
        completed, written = _run_backend(tmp_path)

        assert completed.returncode == 0, completed.stderr
        assert "SCCACHE_GHA_ENABLED=true" in written
        assert _reported_backend(completed) == "gha"

    @pytest.mark.parametrize("value", ["true", "false", ""])
    def test_respects_a_caller_that_chose(self, tmp_path: Path, value: str) -> None:
        """`false` is a choice, and an empty value is one too.

        A caller who disabled the backend deliberately must not have it turned
        back on, which is why the guard tests for the variable being set at all
        rather than for a truthy value.
        """
        completed, written = _run_backend(tmp_path, gha_enabled=value)

        assert completed.returncode == 0, completed.stderr
        assert written == ""
        assert _reported_backend(completed) == "caller"
        assert "already set" in completed.stdout

    def test_leaves_a_caller_owned_directory_alone(self, tmp_path: Path) -> None:
        """`SCCACHE_DIR` means the caller mounted storage of their own.

        Forcing the GitHub backend would ignore the disk they provided, which
        on a self-hosted runner is the faster of the two.
        """
        completed, written = _run_backend(tmp_path, sccache_dir=str(tmp_path))

        assert completed.returncode == 0, completed.stderr
        assert written == ""
        assert _reported_backend(completed) == "local"

    def test_an_explicit_choice_beats_a_cache_directory(self, tmp_path: Path) -> None:
        """When both are set, the explicit variable wins and is reported."""
        completed, _written = _run_backend(
            tmp_path, gha_enabled="true", sccache_dir=str(tmp_path)
        )

        assert _reported_backend(completed) == "caller"

    def test_the_metric_names_no_path(self, tmp_path: Path) -> None:
        """A directory in the metric would give the series a path per runner."""
        completed, _written = _run_backend(tmp_path, sccache_dir=str(tmp_path))
        outcome = _reported_backend(completed)

        assert outcome in BACKEND_OUTCOMES
        assert str(tmp_path) not in f"metric setup-rust.sccache.backend={outcome}"


CALLER_VALUES = st.text(st.sampled_from("truefals01 "), min_size=0, max_size=12)


@given(value=CALLER_VALUES)
@settings(max_examples=40, derandomize=True, deadline=None)
def test_any_caller_value_survives(value: str, tmp_path_factory: object) -> None:
    """No value a caller can set may be replaced.

    Whatever they wrote, they wrote it on purpose; the action's job is to
    choose only when nobody has.
    """
    root = typ.cast("pytest.TempPathFactory", tmp_path_factory).mktemp("backend")

    completed, written = _run_backend(root, gha_enabled=value)

    assert completed.returncode == 0, completed.stderr
    assert written == ""
    assert _reported_backend(completed) == "caller"
