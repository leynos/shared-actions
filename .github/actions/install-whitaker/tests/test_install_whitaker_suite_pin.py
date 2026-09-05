"""Exercise the install-whitaker action's lint-suite pin.

The pin is one decision with three observable consequences: the argument the
installer receives, the absence of that argument when unpinned, and the metric
that tells an operator which of those happened. They live together here rather
than among the release-lifecycle tests, which answer a different question.
"""

from __future__ import annotations

import collections.abc as cabc
import typing as typ

import pytest
from _install_scenarios import InstallRun, InstallScenario, run_install_scenario

from composite_fragments import require_posix_host

if typ.TYPE_CHECKING:
    from pathlib import Path

ScenarioRunner = cabc.Callable[[InstallScenario], InstallRun]

require_posix_host()


@pytest.fixture
def run_scenario(tmp_path: Path) -> ScenarioRunner:
    """Return a callable running one scenario under a fresh directory."""

    def _run(scenario: InstallScenario) -> InstallRun:
        return run_install_scenario(tmp_path / "case", scenario)

    return _run


class TestSuitePin:
    """Cover the pin's pass-through, its absence, and its reported mutability."""

    def test_passes_the_suite_pin_to_the_installer(
        self, run_scenario: ScenarioRunner
    ) -> None:
        """The input is worth nothing unless it reaches the installer.

        Asserted on the arguments the installer actually received, because an
        input that is declared, documented and never forwarded looks exactly
        like one that works.
        """
        run = run_scenario(InstallScenario(suite_version="v0.2.7"))

        assert run.result.returncode == 0, run.result.stderr
        assert (
            run.installer_args.read_text(encoding="utf-8").strip()
            == "--suite-version v0.2.7"
        )

    @pytest.mark.parametrize(
        ("suite_version", "metric"),
        [
            pytest.param(
                "4c9a0b6e1d7f2a35c8e0b419d6f7a2c3e5b8d1f0",
                "whitaker-installer.suite=pinned-commit",
                id="commit",
            ),
            pytest.param(
                "v0.2.7",
                "whitaker-installer.suite=pinned-mutable-ref",
                id="tag",
            ),
            pytest.param(
                "main",
                "whitaker-installer.suite=pinned-mutable-ref",
                id="branch",
            ),
            pytest.param(
                "4c9a0b6e1d7f2a35c8e0b419d6f7a2c3e5b8d1f",
                "whitaker-installer.suite=pinned-mutable-ref",
                id="short-commit",
            ),
        ],
    )
    def test_reports_whether_the_pin_can_move(
        self,
        run_scenario: ScenarioRunner,
        suite_version: str,
        metric: str,
    ) -> None:
        """Distinguish an immutable pin from one that can advance silently.

        A branch or tag reported as simply "pinned" would claim a protection
        the lane does not have: both can move without the calling repository
        changing a line, which is the drift this metric exists to expose.
        """
        run = run_scenario(InstallScenario(suite_version=suite_version))

        assert run.result.returncode == 0, run.result.stderr
        assert metric in run.summary_lines()

    def test_passes_no_suite_argument_when_unpinned(
        self, run_scenario: ScenarioRunner
    ) -> None:
        """The default must stay the installer's own default.

        Passing an empty `--suite-version` would be a pin to the empty string
        rather than an absence, and macOS runners ship bash 3.2, where an
        empty array expanded under `set -u` is an error rather than nothing.
        """
        run = run_scenario(InstallScenario())

        assert run.result.returncode == 0, run.result.stderr
        assert run.installer_args.read_text(encoding="utf-8").strip() == ""
        assert "whitaker-installer.suite=default-branch-tip" in run.summary_lines()


class TestSuiteSource:
    """Cover the outcome CI exists to refuse: a silent source build."""

    def test_a_prebuilt_install_is_reported_as_prebuilt(
        self, run_scenario: ScenarioRunner
    ) -> None:
        """The ordinary path must be visible, not only the failure."""
        run = run_scenario(InstallScenario(ci_mode="true"))

        assert run.result.returncode == 0, run.result.stderr
        assert "whitaker-installer.suite-source=prebuilt" in run.summary_lines()

    def test_a_source_build_fails_the_step_in_ci_mode(
        self, run_scenario: ScenarioRunner
    ) -> None:
        """A source build succeeds, and that is the problem.

        The installer exits zero after falling back to `cargo install`, so a
        run that built its lint tooling from source looks like a working run
        while having tested something else, more slowly. CI must refuse it.
        """
        run = run_scenario(
            InstallScenario(ci_mode="true", installer_source_fallback=True)
        )

        assert run.result.returncode != 0
        assert "whitaker-installer.suite-source=source" in run.summary_lines()
        assert "built from source" in run.result.stderr
        assert "whitaker-installer.result=success" not in run.summary_lines(), (
            "a source build must not be recorded as a successful install"
        )

    def test_a_source_build_is_reported_but_allowed_outside_ci_mode(
        self, run_scenario: ScenarioRunner
    ) -> None:
        """Local reproduction may legitimately build from source."""
        run = run_scenario(
            InstallScenario(ci_mode="false", installer_source_fallback=True)
        )

        assert run.result.returncode == 0, run.result.stderr
        assert "whitaker-installer.suite-source=source" in run.summary_lines()
        assert "whitaker-installer.result=success" in run.summary_lines()
