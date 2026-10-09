"""Exercise the install-mdtablefix lifecycle against a stubbed Cargo.

Every test drives the action's real Bash fragments in manifest order, so each
outcome asserted here, and the single bounded metric that names it, is the one
a runner would produce.
"""

from __future__ import annotations

import typing as typ

import pytest
from _mdtablefix_manifest import (
    SUPPORTED_PLATFORMS,
    UNSUPPORTED_PLATFORMS,
)
from _mdtablefix_scenarios import (
    Scenario,
    ScenarioResult,
    cached_digest,
    installed_digest,
    run_scenario,
)

from composite_fragments import require_posix_host

if typ.TYPE_CHECKING:
    from pathlib import Path

require_posix_host()


_FAILURE_ANNOTATION = "::error title=Install mdtablefix failed::"


def _assert_metric(result: ScenarioResult, expected: str) -> None:
    """Assert ``expected`` is among the metrics the run emitted."""
    assert expected in result.metrics(), (
        f"expected {expected!r} among the metrics, got {result.metrics()}"
    )


def _assert_only_metric(result: ScenarioResult, expected: str) -> None:
    """Assert the run emitted ``expected`` and nothing else."""
    assert result.metrics() == (expected,), (
        f"expected only {expected!r}, got {result.metrics()}"
    )


def _assert_only_metric_result(result: ScenarioResult, expected: str) -> None:
    """Assert ``expected`` is the only outcome metric the run emitted."""
    results = [
        line
        for line in result.metrics()
        if line.startswith("install-mdtablefix.result=")
    ]
    assert results == [expected], f"expected only {expected!r}, got {results}"


def _assert_annotated(result: ScenarioResult) -> None:
    """Assert the run annotated its failure for the runner's log."""
    assert _FAILURE_ANNOTATION in result.stderr, (
        f"expected a failure annotation, got {result.stderr!r}"
    )


def _assert_installed(result: ScenarioResult, version: str) -> None:
    """Assert the run installed and verified ``version``."""
    assert result.returncode == 0, f"the install failed: {result.stderr}"
    _assert_metric(result, "install-mdtablefix.result=installed")
    assert result.installed_version == version, (
        f"expected mdtablefix {version}, got {result.installed_version}"
    )


def _assert_nothing_installed(result: ScenarioResult) -> None:
    """Assert the run left no executable behind."""
    assert result.installed_output is None, (
        f"an executable was left behind reporting {result.installed_output!r}"
    )


class TestCachedOutcome:
    """Validate the early exit when the pinned version is already present."""

    def test_reports_cached_and_installs_nothing(self, tmp_path: Path) -> None:
        """Verify a matching executable short-circuits the install."""
        result = run_scenario(Scenario(tmp_path=tmp_path, cached_version="0.5.1"))

        assert result.returncode == 0, f"a cache hit must succeed: {result.stderr}"
        _assert_only_metric(result, "install-mdtablefix.result=cached")
        assert result.cargo_log == "", (
            f"a cache hit must not call cargo: {result.cargo_log!r}"
        )
        assert "Install mdtablefix" not in result.executed(), (
            f"the install step ran despite a cache hit: {result.executed()}"
        )

    def test_still_exports_the_bin_directory(self, tmp_path: Path) -> None:
        """Verify a cached run leaves the executable on the job's PATH."""
        result = run_scenario(Scenario(tmp_path=tmp_path, cached_version="0.5.1"))

        assert result.github_path.strip().endswith("/.local/bin"), (
            f"bin-dir was not added to GITHUB_PATH: {result.github_path!r}"
        )

    def test_replaces_an_executable_of_another_version(self, tmp_path: Path) -> None:
        """Verify a stale executable is reinstalled rather than trusted."""
        result = run_scenario(Scenario(tmp_path=tmp_path, cached_version="0.4.0"))

        _assert_installed(result, "0.5.1")


class TestBinstallProvisioning:
    """Validate how the action obtains cargo-binstall."""

    def test_reuses_a_working_binstall(self, tmp_path: Path) -> None:
        """Verify a usable cargo-binstall is not reinstalled."""
        result = run_scenario(Scenario(tmp_path=tmp_path, binstall_present=True))

        _assert_installed(result, "0.5.1")
        assert "Install cargo-binstall" not in result.executed(), (
            f"a usable cargo-binstall was reinstalled: {result.executed()}"
        )
        _assert_metric(result, "install-mdtablefix.binstall=present")

    def test_installs_binstall_when_the_probe_cannot_run_it(
        self,
        tmp_path: Path,
    ) -> None:
        """Verify a missing cargo-binstall is provisioned before the install."""
        result = run_scenario(Scenario(tmp_path=tmp_path, binstall_present=False))

        _assert_installed(result, "0.5.1")
        _assert_metric(result, "install-mdtablefix.binstall=installed")

    def test_reports_a_failed_provisioning(self, tmp_path: Path) -> None:
        """Verify a failed upstream installer still names one outcome."""
        result = run_scenario(
            Scenario(
                tmp_path=tmp_path,
                binstall_present=False,
                binstall_install_fails=True,
            ),
        )

        assert result.returncode != 0, "a failed provisioning must fail the job"
        _assert_only_metric(result, "install-mdtablefix.result=binstall-unavailable")
        _assert_annotated(result)
        _assert_nothing_installed(result)

    def test_does_not_provision_binstall_for_a_cached_executable(
        self,
        tmp_path: Path,
    ) -> None:
        """Verify a cache hit skips cargo-binstall entirely."""
        result = run_scenario(
            Scenario(tmp_path=tmp_path, binstall_present=False, cached_version="0.5.1"),
        )

        _assert_only_metric(result, "install-mdtablefix.result=cached")


class TestHardenedInstall:
    """Validate the cargo-binstall invocation the action issues."""

    def test_passes_every_hardening_flag(self, tmp_path: Path) -> None:
        """Verify the invocation recorded by the stub."""
        result = run_scenario(Scenario(tmp_path=tmp_path))

        assert result.returncode == 0, f"the install failed: {result.stderr}"
        invocation = result.cargo_log.strip()
        for flag in (
            "--no-confirm",
            "--locked",
            "--disable-strategies compile",
            "--disable-telemetry",
            "mdtablefix@0.5.1",
        ):
            assert flag in invocation, f"{flag!r} missing from {invocation!r}"
        assert "--bin-dir" not in invocation, (
            f"the retired 0.5.0 bin-dir override reappeared in {invocation!r}"
        )

    def test_fails_closed_when_no_prebuilt_asset_exists(self, tmp_path: Path) -> None:
        """Verify a binstall failure stops the job rather than compiling."""
        result = run_scenario(Scenario(tmp_path=tmp_path, binstall_fails=True))

        assert result.returncode == 94, (
            f"binstall's exit code must propagate: {result.returncode}"
        )
        _assert_metric(result, "install-mdtablefix.result=install-failed")
        _assert_annotated(result)
        _assert_nothing_installed(result)

    def test_reports_a_version_mismatch(self, tmp_path: Path) -> None:
        """Verify an executable of the wrong version fails the job."""
        result = run_scenario(Scenario(tmp_path=tmp_path, installs_version="0.4.0"))

        assert result.returncode == 1, (
            f"a version mismatch must fail the job: {result.returncode}"
        )
        _assert_metric(result, "install-mdtablefix.result=version-mismatch")
        assert "but it reported mdtablefix 0.4.0" in result.stderr, (
            f"the annotation must name both versions: {result.stderr!r}"
        )

    def test_notices_a_success_that_installed_nothing(self, tmp_path: Path) -> None:
        """Verify a binstall that exits zero without writing is not believed."""
        result = run_scenario(
            Scenario(tmp_path=tmp_path, install_creates_executable=False),
        )

        assert result.returncode == 1, (
            f"an empty install must fail the job: {result.returncode}"
        )
        _assert_metric(result, "install-mdtablefix.result=version-mismatch")
        assert "no executable was installed at" in result.stderr, (
            f"the annotation must say nothing was installed: {result.stderr!r}"
        )

    def test_ignores_output_after_the_first_line(self, tmp_path: Path) -> None:
        """Verify trailing lines from the executable do not fail a good install.

        Some tools print a banner or a build line after the version. Only the
        first line carries the version, so only the first line is compared.
        """
        result = run_scenario(
            Scenario(
                tmp_path=tmp_path,
                installs_output="mdtablefix 0.5.1\nbuilt from deadbeef\n",
            ),
        )

        _assert_installed(result, "0.5.1")

    def test_bounds_the_reported_version_in_the_annotation(
        self,
        tmp_path: Path,
    ) -> None:
        """Verify an overlong report is truncated before it reaches the log.

        What sits in `bin-dir` came out of the caller's cache, so its output is
        not this action's to copy into an annotation whole.
        """
        overlong = "mdtablefix 0.5.1" + "z" * 200
        result = run_scenario(Scenario(tmp_path=tmp_path, installs_output=overlong))

        assert result.returncode == 1, (
            "an executable reporting an unexpected version must fail the job"
        )
        _assert_metric(result, "install-mdtablefix.result=version-mismatch")
        assert overlong not in result.stderr, (
            "the whole reported line reached the annotation unbounded"
        )
        assert overlong[:120] in result.stderr, (
            f"the truncated report did not reach the annotation: {result.stderr!r}"
        )

    def test_a_cached_executable_reporting_too_much_is_reinstalled(
        self,
        tmp_path: Path,
    ) -> None:
        """Verify the probe bounds what it trusts from the caller's cache."""
        result = run_scenario(
            Scenario(
                tmp_path=tmp_path,
                cached_output="mdtablefix 0.5.1" + "z" * 200,
            ),
        )

        _assert_installed(result, "0.5.1")

    def test_a_cached_executable_may_print_more_than_one_line(
        self,
        tmp_path: Path,
    ) -> None:
        """Verify the probe reads the version line and ignores what follows."""
        result = run_scenario(
            Scenario(
                tmp_path=tmp_path,
                cached_output="mdtablefix 0.5.1\nbuilt from deadbeef\n",
            ),
        )

        _assert_only_metric(result, "install-mdtablefix.result=cached")
        assert result.cargo_log == "", (
            f"a cache hit must not call cargo: {result.cargo_log!r}"
        )

    def test_emits_exactly_one_result_metric(self, tmp_path: Path) -> None:
        """Verify the outcome vocabulary stays bounded and unambiguous."""
        result = run_scenario(Scenario(tmp_path=tmp_path))

        results = [
            line
            for line in result.metrics()
            if line.startswith("install-mdtablefix.result=")
        ]
        assert results == ["install-mdtablefix.result=installed"], (
            f"a run must report exactly one outcome, got {results}"
        )


@pytest.mark.parametrize("platform", SUPPORTED_PLATFORMS)
def test_supported_platform_installs(tmp_path: Path, platform: str) -> None:
    """Verify every platform with a prebuilt release installs."""
    runner_os, _, runner_arch = platform.partition(":")
    result = run_scenario(
        Scenario(tmp_path=tmp_path, runner_os=runner_os, runner_arch=runner_arch),
    )

    _assert_installed(result, "0.5.1")


@pytest.mark.parametrize("platform", UNSUPPORTED_PLATFORMS)
def test_unsupported_platform_fails_closed(tmp_path: Path, platform: str) -> None:
    """Verify a platform with no prebuilt release never reaches Cargo.

    mdtablefix 0.5.1 publishes archives for Linux and macOS on x86_64 and
    aarch64, and for Windows on x86_64 only. A 32-bit Linux or an aarch64
    Windows runner has no asset, so the only way to satisfy it is a source
    build, which this action never performs.
    """
    runner_os, _, runner_arch = platform.partition(":")
    result = run_scenario(
        Scenario(tmp_path=tmp_path, runner_os=runner_os, runner_arch=runner_arch),
    )

    assert result.returncode == 1, f"{platform} did not fail closed: {result.stderr}"
    _assert_only_metric(result, "install-mdtablefix.result=no-prebuilt")
    assert "publishes no prebuilt release" in result.stderr, (
        f"{platform} was rejected without a reason: {result.stderr!r}"
    )
    assert result.cargo_log == "", f"{platform} reached cargo: {result.cargo_log!r}"


def test_unsupported_platform_ignores_a_cached_executable(tmp_path: Path) -> None:
    """Verify a cached executable cannot rescue an unsupported platform.

    Windows aarch64 rather than macOS: 0.5.1 publishes macOS archives, so the
    platform that used to make this point is now a supported one.
    """
    result = run_scenario(
        Scenario(
            tmp_path=tmp_path,
            runner_os="Windows",
            runner_arch="ARM64",
            cached_version="0.5.1",
        ),
    )

    assert result.returncode == 1, (
        f"a cached executable rescued an unsupported platform: {result.stderr}"
    )
    _assert_only_metric(result, "install-mdtablefix.result=no-prebuilt")


_PUBLISHED_ASSETS = {
    "Linux:X64": "mdtablefix-linux-x86_64",
    "Linux:ARM64": "mdtablefix-linux-aarch64",
    "macOS:X64": "mdtablefix-macos-x86_64",
    "macOS:ARM64": "mdtablefix-macos-aarch64",
    "Windows:X64": "mdtablefix-windows-x86_64.exe",
}


class TestChecksumVerification:
    """Validate the comparison with the checksum the release publishes."""

    def test_reports_a_verified_install(self, tmp_path: Path) -> None:
        """Verify a matching digest is reported beside the install result."""
        result = run_scenario(Scenario(tmp_path=tmp_path))

        _assert_installed(result, "0.5.1")
        _assert_metric(result, "install-mdtablefix.checksum=published")
        assert "Verify mdtablefix checksum" in result.executed(), (
            f"the checksum step did not run: {result.executed()}"
        )

    @pytest.mark.parametrize("platform", SUPPORTED_PLATFORMS)
    def test_fetches_the_digest_published_for_the_platform(
        self,
        tmp_path: Path,
        platform: str,
    ) -> None:
        """Verify each platform is checked against its own bare asset."""
        runner_os, _, runner_arch = platform.partition(":")
        result = run_scenario(
            Scenario(tmp_path=tmp_path, runner_os=runner_os, runner_arch=runner_arch),
        )

        expected = (
            "https://github.com/leynos/mdtablefix/releases/download/v0.5.1/"
            f"{_PUBLISHED_ASSETS[platform]}.sha256"
        )
        assert expected in result.curl_log, (
            f"expected a request for {expected}, got {result.curl_log!r}"
        )
        assert "--proto =https" in result.curl_log, (
            f"the download must be HTTPS only: {result.curl_log!r}"
        )

    def test_fails_when_the_digest_differs_and_removes_the_executable(
        self,
        tmp_path: Path,
    ) -> None:
        """Verify an executable that fails verification is neither used nor kept."""
        result = run_scenario(Scenario(tmp_path=tmp_path, checksum="mismatch"))

        assert result.returncode == 1, f"a digest mismatch passed: {result.stderr}"
        _assert_annotated(result)
        assert "does not match the published" in result.stderr, (
            f"the mismatch was not explained: {result.stderr!r}"
        )
        _assert_only_metric_result(result, "install-mdtablefix.result=checksum-failed")
        _assert_nothing_installed(result)
        assert "Verify mdtablefix" not in result.executed(), (
            f"the version check ran on an unverified executable: {result.executed()}"
        )

    @pytest.mark.parametrize("checksum", ["unreachable", "malformed"])
    def test_fails_closed_without_a_usable_published_digest(
        self,
        tmp_path: Path,
        checksum: str,
    ) -> None:
        """Verify no published digest means no install, never a skipped check."""
        result = run_scenario(Scenario(tmp_path=tmp_path, checksum=checksum))

        assert result.returncode == 1, f"{checksum} digest passed: {result.stderr}"
        _assert_annotated(result)
        _assert_only_metric_result(result, "install-mdtablefix.result=checksum-failed")

    def test_removes_the_executable_when_no_digest_could_be_obtained(
        self,
        tmp_path: Path,
    ) -> None:
        """Verify an unverifiable executable is not left on PATH."""
        for checksum in ("unreachable", "malformed"):
            result = run_scenario(
                Scenario(tmp_path=tmp_path / checksum, checksum=checksum)
            )

            _assert_nothing_installed(result)

    def test_a_pinned_digest_replaces_the_published_one(self, tmp_path: Path) -> None:
        """Verify a caller's digest is checked without any download."""
        scenario = Scenario(tmp_path=tmp_path)
        pinned = Scenario(tmp_path=tmp_path, sha256=installed_digest(scenario).upper())
        result = run_scenario(pinned)

        _assert_installed(result, "0.5.1")
        _assert_metric(result, "install-mdtablefix.checksum=pinned")
        assert result.curl_log == "", (
            f"a pinned digest must not fetch the published one: {result.curl_log!r}"
        )

    def test_a_pinned_digest_wins_over_a_matching_published_one(
        self,
        tmp_path: Path,
    ) -> None:
        """Verify the pin is authoritative: the release's digest cannot rescue it."""
        result = run_scenario(Scenario(tmp_path=tmp_path, sha256="0" * 64))

        assert result.returncode == 1, f"a wrong pinned digest passed: {result.stderr}"
        assert "does not match the pinned" in result.stderr, (
            f"the mismatch was not attributed to the pin: {result.stderr!r}"
        )
        _assert_only_metric_result(result, "install-mdtablefix.result=checksum-failed")
        _assert_nothing_installed(result)
        assert result.curl_log == "", f"the pin fetched a digest: {result.curl_log!r}"

    @pytest.mark.parametrize("value", ["abc", "g" * 64, "0" * 63, "0" * 65])
    def test_refuses_a_malformed_pinned_digest(
        self,
        tmp_path: Path,
        value: str,
    ) -> None:
        """Verify a bad ``sha256`` input is refused before anything runs."""
        result = run_scenario(Scenario(tmp_path=tmp_path, sha256=value))

        assert result.returncode == 1, f"{value!r} was accepted: {result.stderr}"
        _assert_only_metric(result, "install-mdtablefix.result=invalid-input")
        assert result.cargo_log == "", f"{value!r} reached cargo"

    def test_the_digest_download_is_https_tls12_and_retried_once_per_run(
        self,
        tmp_path: Path,
    ) -> None:
        """Verify the request flags and that exactly one request is made."""
        result = run_scenario(Scenario(tmp_path=tmp_path))

        requests = result.curl_log.splitlines()
        assert len(requests) == 1, f"expected one request, got {requests}"
        for flag in ("--fail", "--proto =https", "--tlsv1.2", "--retry 3"):
            assert flag in requests[0], f"missing {flag!r} in {requests[0]!r}"

    def test_a_failing_hash_tool_fails_closed_and_removes_the_executable(
        self,
        tmp_path: Path,
    ) -> None:
        """Verify a hash command that errors reaches the checksum-failed boundary."""
        result = run_scenario(Scenario(tmp_path=tmp_path, sha256sum_fails=True))

        assert result.returncode == 1, f"a hashing failure passed: {result.stderr}"
        _assert_annotated(result)
        _assert_only_metric_result(result, "install-mdtablefix.result=checksum-failed")
        _assert_nothing_installed(result)

    def test_a_cached_executable_matching_the_pin_is_used(self, tmp_path: Path) -> None:
        """Verify the pin is checked on a cache hit, and a match stays cached."""
        base = Scenario(tmp_path=tmp_path, cached_version="0.5.1")
        result = run_scenario(
            Scenario(
                tmp_path=tmp_path, cached_version="0.5.1", sha256=cached_digest(base)
            ),
        )

        _assert_only_metric(result, "install-mdtablefix.result=cached")
        assert result.cargo_log == "", (
            f"a pinned cache hit reinstalled: {result.cargo_log!r}"
        )

    def test_a_cached_executable_not_matching_the_pin_is_replaced(
        self,
        tmp_path: Path,
    ) -> None:
        """Verify an unpinned cached executable is not run, and is reinstalled."""
        pinned = installed_digest(Scenario(tmp_path=tmp_path))
        result = run_scenario(
            Scenario(
                tmp_path=tmp_path,
                cached_output="mdtablefix 0.5.1\nbuilt elsewhere",
                sha256=pinned,
            ),
        )

        _assert_installed(result, "0.5.1")
        _assert_metric(result, "install-mdtablefix.checksum=pinned")
        assert result.cargo_log != "", "a cached executable that failed the pin stayed"

    def test_a_cache_hit_downloads_nothing(self, tmp_path: Path) -> None:
        """Verify the cached path fetches no checksum and reports none."""
        result = run_scenario(Scenario(tmp_path=tmp_path, cached_version="0.5.1"))

        assert result.curl_log == "", f"a cache hit called curl: {result.curl_log!r}"
        _assert_only_metric(result, "install-mdtablefix.result=cached")

    def test_leaves_a_missing_executable_to_the_version_check(
        self,
        tmp_path: Path,
    ) -> None:
        """Verify "installed nothing" keeps its single, existing outcome."""
        result = run_scenario(
            Scenario(tmp_path=tmp_path, install_creates_executable=False),
        )

        assert result.returncode == 1, "an install that wrote nothing passed"
        _assert_only_metric_result(result, "install-mdtablefix.result=version-mismatch")
        assert result.curl_log == "", (
            f"nothing installed, so nothing to verify: {result.curl_log!r}"
        )
