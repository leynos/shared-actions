"""Exercise the install-whitaker action's release lifecycle end to end.

Each test runs the real validation and lifecycle fragments in sequence against
stubbed ``curl``, ``tar``, and ``unzip`` commands, threading step outputs
between fragments exactly as a composite action would. That covers platform
selection, the pinned trust anchor, the cache paths, and the failure contract
without any network access.
"""

from __future__ import annotations

import collections.abc as cabc
import io
import string
import tarfile
import typing as typ
import zipfile
from pathlib import PurePosixPath

import pytest
from _action_manifest import SUPPORTED_PLATFORMS
from _install_scenarios import (
    InstallRun,
    InstallScenario,
    archive_fixture,
    run_install_scenario,
    run_named_steps,
)
from hypothesis import given, settings
from hypothesis import strategies as st

if typ.TYPE_CHECKING:
    from pathlib import Path

ScenarioRunner = cabc.Callable[[InstallScenario], InstallRun]

_WRONG_SHA256 = "0" * 64
_PROPERTY_SETTINGS = settings(deadline=None, derandomize=True, max_examples=25)
_VALID_INSTALLER_VERSIONS = st.lists(
    st.from_regex(r"0|[1-9][0-9]{0,2}", fullmatch=True),
    min_size=1,
    max_size=3,
).map(".".join)
_WINDOWS_RESERVED_SEGMENTS = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{number}" for number in range(1, 10)),
        *(f"LPT{number}" for number in range(1, 10)),
    },
)
_SAFE_CARGO_HOME_SEGMENTS = st.text(
    alphabet=string.ascii_letters + string.digits + "_-",
    min_size=1,
    max_size=16,
).filter(lambda segment: segment.upper() not in _WINDOWS_RESERVED_SEGMENTS)


@pytest.fixture
def run_scenario(tmp_path: Path) -> ScenarioRunner:
    """Return a callable running one scenario under a fresh directory."""

    def _run(scenario: InstallScenario) -> InstallRun:
        return run_install_scenario(tmp_path / "case", scenario)

    return _run


class TestInstallation:
    """Check the successful installation and cache-reuse paths."""

    def test_installs_the_pinned_official_release(
        self, run_scenario: ScenarioRunner
    ) -> None:
        """Verify a cache miss downloads, verifies, and installs the release."""
        run = run_scenario(InstallScenario())

        assert run.result.returncode == 0, run.result.stderr
        scenario = InstallScenario()
        download_log = run.download_log.read_text(encoding="utf-8")
        assert f"/v0.2.7/{scenario.asset} " in download_log
        assert f"/v0.2.7/{scenario.asset}.sha256 " in download_log
        assert run.installer_path.is_file()
        assert run.installer_log.read_text(encoding="utf-8") == "suite installed\n"
        assert run.lifecycle_metrics() == [
            "whitaker-installer.cache=miss",
            "whitaker-installer.digest=verified",
            "whitaker-installer.trust-anchor=pinned",
            "whitaker-installer.path=official-release",
            "whitaker-installer.result=success",
        ]
        assert (
            "::notice title=Whitaker installer::path=official-release version=0.2.7"
            in run.result.stdout
        )

    def test_reuses_a_restored_installer(self, run_scenario: ScenarioRunner) -> None:
        """Verify a restored installer bypasses the release download."""
        run = run_scenario(
            InstallScenario(installer_present=True, cache_hit=True),
        )

        assert run.result.returncode == 0, run.result.stderr
        assert not run.download_log.exists()
        assert run.installer_log.read_text(encoding="utf-8") == "suite installed\n"
        assert run.summary_lines() == [
            "whitaker-installer.cache=hit",
            "whitaker-installer.path=cache",
            "whitaker-installer.result=success",
        ]
        assert run.transfer_metrics() == []

    def test_reports_caller_owned_cache_ownership(
        self, run_scenario: ScenarioRunner
    ) -> None:
        """Verify external cache ownership disables the built-in cache."""
        run = run_scenario(
            InstallScenario(installer_present=True, cache_provider="external"),
        )

        assert run.result.returncode == 0, run.result.stderr
        assert "provider=external state=disabled" in run.result.stdout
        assert run.summary_lines()[0] == "whitaker-installer.cache=disabled"

    def test_installs_into_a_custom_cargo_home(
        self, run_scenario: ScenarioRunner
    ) -> None:
        """Verify a non-default Cargo home receives the requested version."""
        run = run_scenario(
            InstallScenario(
                installer_version="0.2.6",
                cargo_home_name="custom-cargo-home",
            ),
        )

        assert run.result.returncode == 0, run.result.stderr
        assert run.installer_path.is_file()
        assert run.installer_path.parent.parent.name == "custom-cargo-home"
        assert "/v0.2.6/" in run.download_log.read_text(encoding="utf-8")
        assert "state=miss version=0.2.6" in run.result.stdout

    def test_prefers_the_cargo_home_installer_over_the_ambient_path(
        self,
        run_scenario: ScenarioRunner,
    ) -> None:
        """Verify the resolved installer wins over one earlier on ``PATH``."""
        run = run_scenario(
            InstallScenario(
                installer_present=True,
                cargo_home_name="home/.cargo",
                cargo_home_value="~/.cargo",
                conflicting_installer=True,
            ),
        )

        assert run.result.returncode == 0, run.result.stderr
        assert run.installer_log.read_text(encoding="utf-8") == "suite installed\n"
        assert not run.conflict_log.exists()
        assert not run.download_log.exists()

    def test_removes_the_staging_directory_after_installing(
        self,
        run_scenario: ScenarioRunner,
    ) -> None:
        """Verify the downloaded archive does not outlive the installation."""
        run = run_scenario(InstallScenario())

        assert run.result.returncode == 0, run.result.stderr
        assert not run.staging_dir.exists()


class TestResolutionPurity:
    """Check that resolution computes without externally visible effects."""

    def test_resolution_writes_no_outputs_metrics_or_annotations(
        self,
        tmp_path: Path,
    ) -> None:
        """Verify the resolve fragment only records what it computed."""
        run = run_named_steps(
            tmp_path / "case",
            InstallScenario(),
            ["Resolve Whitaker release"],
        )

        assert run.result.returncode == 0, run.result.stderr
        assert run.published_output_lines() == []
        assert run.summary_lines() == []
        assert "::notice" not in run.result.stdout
        assert "::error" not in run.result.stderr
        assert not run.download_log.exists()
        recorded = run.resolution_file.read_text(encoding="utf-8").splitlines()
        assert "status=install" in recorded
        assert f"asset={InstallScenario().asset}" in recorded
        assert "trust-anchor=pinned" in recorded

    @pytest.mark.parametrize(
        ("scenario", "expected_kind"),
        [
            pytest.param(
                InstallScenario(pinned_sha256=None),
                "unpinned-digest",
                id="unpinned",
            ),
            pytest.param(
                InstallScenario(installer_sha256="0" * 64),
                "digest-conflict",
                id="conflict",
            ),
            pytest.param(
                InstallScenario(runner_os="Linux", runner_arch="ARM32"),
                "unsupported-runner",
                id="unsupported-runner",
            ),
        ],
    )
    def test_resolution_records_failures_without_reporting_them(
        self,
        tmp_path: Path,
        scenario: InstallScenario,
        expected_kind: str,
    ) -> None:
        """Verify resolution reports a failure to publication, not to the run."""
        run = run_named_steps(
            tmp_path / "case",
            scenario,
            ["Resolve Whitaker release"],
        )

        assert run.result.returncode == 0, run.result.stderr
        assert run.summary_lines() == []
        assert "::error" not in run.result.stderr
        recorded = run.resolution_file.read_text(encoding="utf-8").splitlines()
        assert "status=error" in recorded
        assert f"error-kind={expected_kind}" in recorded


class TestTransferTelemetry:
    """Check the per-transfer telemetry the download step records."""

    def test_reports_each_transfer_once(self, run_scenario: ScenarioRunner) -> None:
        """Verify the archive and its sidecar each report one bounded record."""
        run = run_scenario(InstallScenario())

        assert run.result.returncode == 0, run.result.stderr
        transfers = run.transfer_metrics()
        assert len(transfers) == 2
        assert transfers[0].startswith("whitaker-installer.transfer.archive=ok ")
        assert transfers[1].startswith("whitaker-installer.transfer.sha256=ok ")
        for line in transfers:
            assert "http=200" in line
            assert "attempts=1" in line
            assert "seconds=" in line
            assert "bytes=" in line
        assert (
            run.result.stdout.count(
                "::notice title=Whitaker installer transfer::",
            )
            == 2
        )

    def test_reports_an_unknown_attempt_count_on_older_curl(
        self,
        run_scenario: ScenarioRunner,
    ) -> None:
        """Verify a curl without ``num_retries`` still reports the transfer."""
        run = run_scenario(InstallScenario(curl_version="7.68.0"))

        assert run.result.returncode == 0, run.result.stderr
        transfers = run.transfer_metrics()
        assert len(transfers) == 2
        for line in transfers:
            assert "attempts=unknown" in line


class TestPlatformMatrix:
    """Check asset selection and extraction for every supported runner."""

    @pytest.mark.parametrize(
        ("runner_os", "runner_arch"),
        [tuple(pair.split(":")) for pair in SUPPORTED_PLATFORMS],
    )
    def test_selects_the_platform_release(
        self,
        run_scenario: ScenarioRunner,
        runner_os: str,
        runner_arch: str,
    ) -> None:
        """Verify each runner pair downloads and installs its own asset."""
        scenario = InstallScenario(runner_os=runner_os, runner_arch=runner_arch)
        run = run_scenario(scenario)

        assert run.result.returncode == 0, run.result.stderr
        download_log = run.download_log.read_text(encoding="utf-8")
        assert f"/v0.2.7/{scenario.asset} " in download_log
        assert f"/v0.2.7/{scenario.asset}.sha256 " in download_log
        assert run.installer_path.name == scenario.installer_name
        assert run.installer_path.is_file()
        assert run.installer_log.read_text(encoding="utf-8") == "suite installed\n"

    @pytest.mark.parametrize(
        ("runner_os", "runner_arch"),
        [tuple(pair.split(":")) for pair in SUPPORTED_PLATFORMS],
    )
    def test_extracts_every_archive_format_with_tar(
        self,
        run_scenario: ScenarioRunner,
        runner_os: str,
        runner_arch: str,
    ) -> None:
        """Verify every runner extracts with tar and never calls unzip."""
        scenario = InstallScenario(runner_os=runner_os, runner_arch=runner_arch)
        run = run_scenario(scenario)

        assert run.result.returncode == 0, run.result.stderr
        expected_suffix = ".zip" if runner_os == "Windows" else ".tgz"
        assert scenario.asset.endswith(expected_suffix)
        extract_log = run.extract_log.read_text(encoding="utf-8")
        assert "--strip-components=1" in extract_log
        assert scenario.asset in extract_log
        assert not run.forbidden_log.exists()
        assert run.installer_path.name == (
            "whitaker-installer.exe" if runner_os == "Windows" else "whitaker-installer"
        )

    @pytest.mark.parametrize(
        ("runner_os", "runner_arch"),
        [tuple(pair.split(":")) for pair in SUPPORTED_PLATFORMS],
    )
    def test_fixture_archives_nest_under_one_directory(
        self,
        runner_os: str,
        runner_arch: str,
    ) -> None:
        """Verify the served fixture has the layout ``--strip-components=1`` needs."""
        scenario = InstallScenario(runner_os=runner_os, runner_arch=runner_arch)
        archive = archive_fixture(scenario)

        if scenario.asset.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(archive)) as package:
                names = package.namelist()
        else:
            with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as package:
                names = package.getnames()

        assert len(names) == 1
        top_level = {PurePosixPath(name).parts[0] for name in names}
        assert len(top_level) == 1
        assert PurePosixPath(names[0]).name == scenario.installer_name

    def test_rejects_an_unsupported_runner(self, run_scenario: ScenarioRunner) -> None:
        """Verify an unsupported runner pair fails before any download."""
        run = run_scenario(
            InstallScenario(runner_os="Linux", runner_arch="ARM32"),
        )

        assert run.result.returncode != 0
        assert "unsupported runner Linux/ARM32" in run.result.stderr
        assert not run.download_log.exists()
        assert "whitaker-installer.failure=install" in run.summary_lines()


class TestTrustAnchor:
    """Check that installation depends on an independent pinned digest."""

    def test_rejects_a_mismatched_archive_digest(
        self, run_scenario: ScenarioRunner
    ) -> None:
        """Verify a tampered archive leaves no installer and fails loudly."""
        run = run_scenario(InstallScenario(pinned_sha256=_WRONG_SHA256))

        assert run.result.returncode != 0
        assert "archive digest mismatch" in run.result.stderr
        assert not run.installer_path.exists()
        assert not run.installer_log.exists()
        assert run.lifecycle_metrics() == [
            "whitaker-installer.cache=miss",
            "whitaker-installer.digest=mismatch",
            "whitaker-installer.failure=install",
        ]

    def test_rejects_a_release_sidecar_disagreement(
        self, run_scenario: ScenarioRunner
    ) -> None:
        """Verify the sidecar must agree with the verified archive digest."""
        run = run_scenario(InstallScenario(sidecar_sha256=_WRONG_SHA256))

        assert run.result.returncode != 0
        assert "disagrees with the verified archive digest" in run.result.stderr
        assert not run.installer_path.exists()
        assert "whitaker-installer.digest=sidecar-mismatch" in run.summary_lines()

    def test_the_pinned_manifest_wins_over_a_matching_input(
        self,
        run_scenario: ScenarioRunner,
    ) -> None:
        """Verify a matching supplied digest is accepted as the pinned anchor."""
        scenario = InstallScenario()
        run = run_scenario(
            InstallScenario(installer_sha256=scenario.payload_sha256),
        )

        assert run.result.returncode == 0, run.result.stderr
        assert run.installer_path.is_file()
        assert "whitaker-installer.trust-anchor=pinned" in run.summary_lines()

    def test_rejects_an_input_conflicting_with_the_pinned_manifest(
        self,
        run_scenario: ScenarioRunner,
    ) -> None:
        """Verify a supplied digest cannot override a pinned digest."""
        run = run_scenario(InstallScenario(installer_sha256=_WRONG_SHA256))

        assert run.result.returncode != 0
        assert "conflicts with the digest pinned for" in run.result.stderr
        assert _WRONG_SHA256 in run.result.stderr
        assert InstallScenario().payload_sha256 in run.result.stderr
        assert not run.download_log.exists()
        assert not run.installer_path.exists()
        assert "whitaker-installer.digest=conflict" in run.summary_lines()

    def test_uses_the_input_for_an_unpinned_asset(
        self, run_scenario: ScenarioRunner
    ) -> None:
        """Verify ``installer-sha256`` anchors an asset the manifest omits."""
        scenario = InstallScenario()
        run = run_scenario(
            InstallScenario(
                pinned_sha256=None,
                installer_sha256=scenario.payload_sha256,
            ),
        )

        assert run.result.returncode == 0, run.result.stderr
        assert run.installer_path.is_file()
        assert "whitaker-installer.trust-anchor=input" in run.summary_lines()

    def test_enforces_a_supplied_digest_for_an_unpinned_asset(
        self,
        run_scenario: ScenarioRunner,
    ) -> None:
        """Verify a supplied digest is enforced, not merely recorded."""
        run = run_scenario(
            InstallScenario(pinned_sha256=None, installer_sha256=_WRONG_SHA256),
        )

        assert run.result.returncode != 0
        assert "archive digest mismatch" in run.result.stderr
        assert not run.installer_path.exists()

    def test_fails_closed_without_any_anchor(
        self, run_scenario: ScenarioRunner
    ) -> None:
        """Verify an unpinned asset with no input refuses to download."""
        run = run_scenario(InstallScenario(pinned_sha256=None))

        assert run.result.returncode != 0
        assert "no pinned SHA-256 for" in run.result.stderr
        assert not run.download_log.exists()
        assert not run.installer_path.exists()
        assert "whitaker-installer.digest=unpinned" in run.summary_lines()


def _scenario_should_fail(scenario: InstallScenario) -> bool:
    """Return whether the selected installer path is expected to fail."""
    if scenario.installer_present:
        return scenario.fail_installer
    return scenario.fail_download or scenario.fail_installer


class TestFailureContract:
    """Check the bounded installer-state failure matrix and diagnostics."""

    @pytest.mark.parametrize(
        "scenario",
        [
            InstallScenario(
                installer_present=installer_present,
                fail_download=fail_download,
                fail_installer=fail_installer,
            )
            for installer_present in (False, True)
            for fail_download in (False, True)
            for fail_installer in (False, True)
        ],
    )
    def test_install_scenario_matrix(
        self,
        run_scenario: ScenarioRunner,
        scenario: InstallScenario,
    ) -> None:
        """Exhaustively verify the finite installer-state failure contract."""
        run = run_scenario(scenario)

        assert (run.result.returncode != 0) is _scenario_should_fail(scenario), (
            run.result.stderr
        )

    @pytest.mark.parametrize(
        ("scenario", "expected_error"),
        [
            pytest.param(
                InstallScenario(fail_download=True),
                "Whitaker release download failed",
                id="release-download",
            ),
            pytest.param(
                InstallScenario(fail_installer=True),
                "whitaker-installer failed while installing the Dylint suite",
                id="whitaker-installer",
            ),
        ],
    )
    def test_reports_an_actionable_failure(
        self,
        run_scenario: ScenarioRunner,
        scenario: InstallScenario,
        expected_error: str,
    ) -> None:
        """Verify actionable errors for each selected installer failure path."""
        run = run_scenario(scenario)

        assert run.result.returncode != 0
        assert expected_error in run.result.stderr
        assert (
            f"::error title=Whitaker installer failed::"
            f"exit-code={run.result.returncode} version=0.2.7"
        ) in run.result.stderr
        assert any(
            line.startswith("whitaker-installer.failure=")
            for line in run.summary_lines()
        )


class TestProperties:
    """Check generated installer versions and Cargo-home forms."""

    @_PROPERTY_SETTINGS
    @given(installer_version=_VALID_INSTALLER_VERSIONS)
    def test_installs_generated_valid_versions(
        self,
        tmp_path_factory: pytest.TempPathFactory,
        installer_version: str,
    ) -> None:
        """Verify release URLs accept each generated compatible version."""
        root = tmp_path_factory.mktemp("installer-version-")
        scenario = InstallScenario(installer_version=installer_version)
        run = run_install_scenario(root, scenario)

        assert run.result.returncode == 0, run.result.stderr
        assert f"/v{installer_version}/{scenario.asset} " in run.download_log.read_text(
            encoding="utf-8",
        )
        assert (
            "::notice title=Whitaker installer::status=complete "
            f"version={installer_version}"
        ) in run.result.stdout

    @pytest.mark.parametrize("cargo_home_form", ["absolute", "tilde"])
    @_PROPERTY_SETTINGS
    @given(segment=_SAFE_CARGO_HOME_SEGMENTS)
    def test_reuses_a_cached_installer_for_supported_cargo_home_forms(
        self,
        tmp_path_factory: pytest.TempPathFactory,
        cargo_home_form: str,
        segment: str,
    ) -> None:
        """Verify supported Cargo-home forms select the cached installer first."""
        root = tmp_path_factory.mktemp(f"{cargo_home_form}-cargo-home-")
        if cargo_home_form == "absolute":
            cargo_home_name = f"absolute-cargo/{segment}"
            cargo_home_value = None
        else:
            cargo_home_name = f"home/.cargo/{segment}"
            cargo_home_value = f"~/.cargo/{segment}"

        run = run_install_scenario(
            root,
            InstallScenario(
                installer_present=True,
                cargo_home_name=cargo_home_name,
                cargo_home_value=cargo_home_value,
                conflicting_installer=True,
            ),
        )

        assert run.result.returncode == 0, run.result.stderr
        assert run.installer_path.is_file()
        assert run.installer_log.read_text(encoding="utf-8") == "suite installed\n"
        assert not run.conflict_log.exists()
        assert not run.download_log.exists()
