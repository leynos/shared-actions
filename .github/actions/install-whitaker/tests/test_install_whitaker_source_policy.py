"""Exercise the install-whitaker action's binary-only install policy.

The estate rule has three clauses, and each has an observable consequence in
one lifecycle run: the installer always receives `--no-source-fallback`, a
missing published asset therefore fails the run before Cargo starts, and the
lint suite is never pinned. They live together here rather than among the
release-lifecycle tests, which answer a different question.
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


class TestInstallerArguments:
    """Cover what the installer is told, which is the policy's only carrier."""

    @pytest.mark.parametrize("ci_mode", ["true", "false"])
    def test_always_forbids_the_source_fallback(
        self, run_scenario: ScenarioRunner, ci_mode: str
    ) -> None:
        """The flag reaches the installer whatever `ci-mode` says.

        Asserted on the arguments the installer actually received, and as the
        whole argument list, so neither a dropped flag nor an added suite pin
        can pass.
        """
        run = run_scenario(InstallScenario(ci_mode=ci_mode))

        assert run.result.returncode == 0, run.result.stderr
        assert (
            run.installer_args.read_text(encoding="utf-8").strip()
            == "--no-source-fallback"
        )
        assert "whitaker-installer.suite=default-branch-tip" in run.summary_lines()


class TestCranelift:
    """Cover the one optional installer argument the action forwards."""

    @pytest.mark.parametrize(
        ("cranelift", "arguments"),
        [
            pytest.param("true", "--no-source-fallback --cranelift", id="on"),
            pytest.param("false", "--no-source-fallback", id="off"),
        ],
    )
    def test_forwards_cranelift_only_when_asked(
        self, run_scenario: ScenarioRunner, cranelift: str, arguments: str
    ) -> None:
        """The component is added on request, and the flag is never dropped."""
        run = run_scenario(InstallScenario(cranelift=cranelift))

        assert run.result.returncode == 0, run.result.stderr
        assert run.installer_args.read_text(encoding="utf-8").strip() == arguments


class TestSuitePinRefused:
    """Cover the rolling-release clause: the suite is never pinned."""

    @pytest.mark.parametrize(
        "suite_version",
        [
            pytest.param("4c9a0b6e1d7f2a35c8e0b419d6f7a2c3e5b8d1f0", id="commit"),
            pytest.param("v0.2.8", id="tag"),
            pytest.param("main", id="branch"),
        ],
    )
    @pytest.mark.parametrize("ci_mode", ["true", "false"])
    def test_a_suite_pin_fails_before_anything_is_installed(
        self, run_scenario: ScenarioRunner, suite_version: str, ci_mode: str
    ) -> None:
        """Every ref form is refused, in either mode, before the download.

        A branch, a tag and a commit are all pins; none of them reaches the
        installer, and nothing is fetched on the way to the refusal.
        """
        run = run_scenario(
            InstallScenario(suite_version=suite_version, ci_mode=ci_mode)
        )

        assert run.result.returncode != 0
        assert "suite-version is refused" in run.result.stderr
        assert not run.installer_args.exists(), "the installer must never run"
        assert not run.download_log.exists(), "nothing may be downloaded"


class TestSuiteSource:
    """Cover the outcome CI exists to refuse: a silent source build."""

    def test_a_prebuilt_install_is_reported_as_prebuilt(
        self, run_scenario: ScenarioRunner
    ) -> None:
        """The ordinary path must be visible, not only the failure."""
        run = run_scenario(InstallScenario(ci_mode="true"))

        assert run.result.returncode == 0, run.result.stderr
        assert "whitaker-installer.suite-source=prebuilt" in run.summary_lines()
        assert "whitaker-installer.result=success" in run.summary_lines()

    @pytest.mark.parametrize("ci_mode", ["true", "false"])
    def test_a_missing_asset_fails_before_cargo_starts(
        self, run_scenario: ScenarioRunner, ci_mode: str
    ) -> None:
        """The installer refuses the fallback, so Cargo never runs.

        Holds outside `ci-mode` too: that input now only chooses whether the
        published assets are checked first, never whether a source build is
        acceptable.
        """
        run = run_scenario(
            InstallScenario(ci_mode=ci_mode, installer_source_fallback=True)
        )

        assert run.result.returncode != 0
        assert "source fallback is forbidden" in run.result.stderr
        assert not run.source_build_log.exists(), "Cargo must never start"
        assert not run.installer_log.exists(), "the suite must not appear installed"
        assert "whitaker-installer.result=success" not in run.summary_lines()

    @pytest.mark.parametrize("ci_mode", ["true", "false"])
    def test_a_source_build_that_slips_through_still_fails(
        self, run_scenario: ScenarioRunner, ci_mode: str
    ) -> None:
        """The output backstop fails an installer that ignored the flag.

        A source build exits zero, so a run that built its lint tooling from
        source looks like a working run while having tested something else,
        more slowly. The backstop holds in either mode.
        """
        run = run_scenario(
            InstallScenario(
                ci_mode=ci_mode,
                installer_source_fallback=True,
                ignore_no_source_fallback=True,
            )
        )

        assert run.result.returncode != 0
        assert run.source_build_log.exists()
        assert "whitaker-installer.suite-source=source" in run.summary_lines()
        assert "built from source" in run.result.stderr
        assert "whitaker-installer.result=success" not in run.summary_lines(), (
            "a source build must not be recorded as a successful install"
        )


class TestToolDirectory:
    """Cover the directory the installer puts the Dylint tools in."""

    def test_puts_the_tool_directory_on_path(
        self, run_scenario: ScenarioRunner
    ) -> None:
        """The installer, and every later step, can run what it installed.

        The installer proves cargo-dylint by running `cargo dylint`, which
        finds its subcommand only on PATH. Where the directory was missing,
        on Windows, every run failed that proof and compiled the tool.
        """
        run = run_scenario(InstallScenario())

        assert run.result.returncode == 0, run.result.stderr
        assert run.installer_path_head.endswith("/.local/bin")
        assert run.github_path.read_text(encoding="utf-8").strip() == (
            run.installer_path_head
        )


class TestPublishedAssetCheck:
    """Cover what `ci-mode` still decides: whether assets are checked first."""

    @pytest.mark.parametrize(
        ("ci_mode", "checked"),
        [pytest.param("true", True, id="ci-mode"), pytest.param("false", False)],
    )
    def test_ci_mode_alone_selects_the_pre_check(
        self,
        run_scenario: ScenarioRunner,
        ci_mode: str,
        checked: bool,  # noqa: FBT001 - boolean literals clarify parametrized cases.
    ) -> None:
        """The pre-check runs exactly when `ci-mode` is on."""
        run = run_scenario(InstallScenario(ci_mode=ci_mode))

        assert run.result.returncode == 0, run.result.stderr
        assert run.rolling_check_log.exists() is checked

    def test_a_missing_asset_fails_the_pre_check(
        self, run_scenario: ScenarioRunner
    ) -> None:
        """An absent published asset stops the run before the installer."""
        run = run_scenario(InstallScenario(ci_mode="true", rolling_assets_missing=True))

        assert run.result.returncode != 0
        assert "whitaker-installer.rolling-assets=missing" in run.summary_lines()
        assert not run.installer_args.exists(), "the installer must never run"
