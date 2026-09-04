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
from hypothesis import given, settings
from hypothesis import strategies as st
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
    tmp_path: Path,
    *,
    sccache_path: str | None,
    wrapper: str | None = None,
    sccache_exit: int | None = None,
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
    if sccache_exit is not None:
        environment["FAKE_SCCACHE_EXIT"] = str(sccache_exit)
    completed = subprocess.run(  # noqa: S603,TID251 - exercise the action fragment.
        [requires_bash(), "-c", _export_script()],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
        timeout=10,
    )
    return completed, github_env.read_text(encoding="utf-8")


def _reported_metric(completed: subprocess.CompletedProcess[str]) -> str | None:
    """Return the bounded wrapper outcome the fragment reported."""
    prefix = "metric setup-rust.sccache.wrapper="
    reported = [
        line.removeprefix(prefix)
        for line in completed.stdout.splitlines()
        if line.startswith(prefix)
    ]
    assert len(reported) <= 1, f"more than one wrapper metric: {reported}"
    return reported[0] if reported else None


@pytest.fixture
def fake_sccache(tmp_path: Path) -> Path:
    """Return a stub sccache recording its arguments and the environment file.

    It copies `GITHUB_ENV` as it stood when it ran, so a test can tell whether
    the wrapper was already written by then.
    """
    binary = tmp_path / "sccache"
    binary.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$@" >> "$(dirname "$0")/args.log"\n'
        'cp "$GITHUB_ENV" "$(dirname "$0")/github-env-at-call" 2>/dev/null || true\n'
        'exit "${FAKE_SCCACHE_EXIT:-0}"\n',
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
        """Exporting when sccache never ran would name a binary that is absent.

        The whole predicate is compared, not its parts: a future `||` branch
        could contain both substrings and still export a wrapper on a run where
        no sccache step happened.
        """
        condition = get_step(EXPORT_STEP)["if"]

        assert condition == (
            "${{ inputs.use-sccache == 'true' && github.event_name != 'release' }}"
        )


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


#: Every outcome the export may report. Widening this in the manifest without
#: widening it here breaks a scraper aggregating the series.
WRAPPER_OUTCOMES = frozenset(
    {"exported", "exported-stats-not-zeroed", "caller-set", "missing-sccache-path"}
)


class TestOrdering:
    """The wrapper must be in place before anything else can fail."""

    def test_the_wrapper_is_written_before_the_counters_are_zeroed(
        self, tmp_path: Path, fake_sccache: Path
    ) -> None:
        """A failure while zeroing must not cost the wrapper.

        Zeroing buys a clean baseline for the caller's statistics; the wrapper
        is what makes the cache work at all. Writing it second would trade the
        second for the first.
        """
        completed, _written = _run_export(tmp_path, sccache_path=str(fake_sccache))

        assert completed.returncode == 0, completed.stderr
        seen = (fake_sccache.parent / "github-env-at-call").read_text(encoding="utf-8")
        assert f"RUSTC_WRAPPER={fake_sccache}" in seen

    def test_a_failure_to_zero_keeps_the_wrapper(
        self, tmp_path: Path, fake_sccache: Path
    ) -> None:
        """Losing the baseline is a warning; losing the cache would not be."""
        completed, written = _run_export(
            tmp_path, sccache_path=str(fake_sccache), sccache_exit=1
        )

        assert completed.returncode == 0, completed.stderr
        assert f"RUSTC_WRAPPER={fake_sccache}" in written
        assert "could not zero sccache statistics" in completed.stdout
        assert _reported_metric(completed) == "exported-stats-not-zeroed"


class TestOutcomeMetric:
    """Every terminal path reports one bounded outcome."""

    def test_the_exported_path_reports_itself(
        self, tmp_path: Path, fake_sccache: Path
    ) -> None:
        """The ordinary case must be distinguishable from the others."""
        completed, _written = _run_export(tmp_path, sccache_path=str(fake_sccache))

        assert _reported_metric(completed) == "exported"

    def test_the_caller_set_path_reports_itself(
        self, tmp_path: Path, fake_sccache: Path
    ) -> None:
        """A deferral is not a failure, and must not look like one."""
        completed, _written = _run_export(
            tmp_path, sccache_path=str(fake_sccache), wrapper="/usr/bin/other"
        )

        assert _reported_metric(completed) == "caller-set"

    def test_the_missing_path_reports_itself(self, tmp_path: Path) -> None:
        """The failure carries an outcome too, so it can be counted."""
        completed, _written = _run_export(tmp_path, sccache_path=None)

        assert _reported_metric(completed) == "missing-sccache-path"

    def test_the_metric_carries_no_path_or_value(
        self, tmp_path: Path, fake_sccache: Path
    ) -> None:
        """A metric naming the binary would give the series a path per runner."""
        completed, _written = _run_export(tmp_path, sccache_path=str(fake_sccache))
        outcome = _reported_metric(completed)

        assert outcome in WRAPPER_OUTCOMES
        assert str(fake_sccache) not in f"metric setup-rust.sccache.wrapper={outcome}"


#: Wrapper values a caller may have set. The empty string is the case that
#: matters most: it is a deliberate choice that `-n` would misread as unset.
CALLER_WRAPPERS = st.text(
    st.sampled_from("abcdefghijklmnopqrstuvwxyz/-_. 0123456789"),
    min_size=0,
    max_size=24,
)


class TestCallerOverrideProperty:
    """Whatever the caller set, the action must leave it alone."""

    @given(wrapper=CALLER_WRAPPERS)
    @settings(max_examples=40, derandomize=True, deadline=None)
    def test_any_caller_value_survives(
        self, wrapper: str, tmp_path_factory: object
    ) -> None:
        """No value a caller can set may be replaced, empty included."""
        root = typ.cast("pytest.TempPathFactory", tmp_path_factory).mktemp("wrap")
        binary = root / "sccache"
        binary.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        binary.chmod(0o755)

        completed, written = _run_export(
            root, sccache_path=str(binary), wrapper=wrapper
        )

        assert completed.returncode == 0, completed.stderr
        assert written == ""
        assert _reported_metric(completed) == "caller-set"
        assert "already set" in completed.stdout
