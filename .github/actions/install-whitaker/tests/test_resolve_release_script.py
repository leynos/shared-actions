"""Exercise the pure release-resolution script directly.

``scripts/resolve-release.sh`` is the query half of the action's resolution: it
reads the environment and prints a record, and does nothing else. Running it
here without the surrounding composite step proves that directly, rather than
inferring it from the step that adapts it.
"""

from __future__ import annotations

import subprocess
import typing as typ

import pytest
from _action_manifest import RESOLVE_SCRIPT_PATH, asset_name
from _fragment_runner import (
    ambient_env,
    bash_executable,
    require_posix_host,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

_PINNED = "a" * 64
_SUPPLIED = "b" * 64
_VERSION = "0.2.7"

require_posix_host()


def _run_resolution(
    tmp_path: Path, **overrides: str
) -> subprocess.CompletedProcess[str]:
    """Run the resolution script with a complete, overridable environment."""
    manifest = tmp_path / "installer-digests.sha256"
    if "WHITAKER_DIGEST_MANIFEST" not in overrides:
        asset = asset_name("Linux", "X64", _VERSION)
        manifest.write_text(f"{_PINNED}  {asset}\n", encoding="utf-8")
    env = {
        **ambient_env(),
        "RUNNER_ARCHITECTURE": "X64",
        "RUNNER_OPERATING_SYSTEM": "Linux",
        "WHITAKER_DIGEST_MANIFEST": str(manifest),
        "WHITAKER_INSTALLER_PATH": str(tmp_path / "bin" / "whitaker-installer"),
        "WHITAKER_INSTALLER_SHA256": "",
        "WHITAKER_INSTALLER_VERSION": _VERSION,
        "WHITAKER_INSTALLER_VERSION_PATH": str(
            tmp_path / "bin" / ".whitaker-installer-version",
        ),
        "WHITAKER_STAGING_DIR": str(tmp_path / "staging"),
        **overrides,
    }
    return subprocess.run(  # noqa: S603,TID251 - exercise the shipped script.
        [bash_executable(), str(RESOLVE_SCRIPT_PATH)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )


def _record(process: subprocess.CompletedProcess[str]) -> dict[str, str]:
    """Parse the printed record into its fields."""
    return dict(
        line.split("=", 1) for line in process.stdout.splitlines() if "=" in line
    )


def _seed_cached_installer(tmp_path: Path, version: str | None) -> None:
    """Place an executable installer, optionally with a version marker."""
    installer = tmp_path / "bin" / "whitaker-installer"
    installer.parent.mkdir(parents=True, exist_ok=True)
    installer.write_text("#!/bin/sh\n", encoding="utf-8")
    installer.chmod(0o755)
    if version is not None:
        (tmp_path / "bin" / ".whitaker-installer-version").write_text(
            f"{version}\n",
            encoding="utf-8",
        )


class TestPurity:
    """Check that the query only prints."""

    def test_writes_nothing_and_annotates_nothing(self, tmp_path: Path) -> None:
        """Verify the script leaves no trace beyond its printed record."""
        manifest = tmp_path / "pinned.sha256"
        manifest.write_text(
            f"{_PINNED}  {asset_name('Linux', 'X64', _VERSION)}\n",
            encoding="utf-8",
        )
        summary = tmp_path / "summary.md"
        output = tmp_path / "output"
        before = sorted(path.name for path in tmp_path.iterdir())

        result = _run_resolution(
            tmp_path,
            WHITAKER_DIGEST_MANIFEST=str(manifest),
            GITHUB_STEP_SUMMARY=str(summary),
            GITHUB_OUTPUT=str(output),
        )

        assert result.returncode == 0, result.stderr
        assert result.stderr == ""
        assert "::notice" not in result.stdout
        assert "::error" not in result.stdout
        assert not summary.exists()
        assert not output.exists()
        assert sorted(path.name for path in tmp_path.iterdir()) == before


class TestResolution:
    """Check the record the query prints for each outcome."""

    def test_resolves_a_pinned_release(self, tmp_path: Path) -> None:
        """Verify a pinned asset resolves with the manifest as the anchor."""
        result = _run_resolution(tmp_path)

        record = _record(result)
        assert record["status"] == "install"
        assert record["asset"] == asset_name("Linux", "X64", _VERSION)
        assert record["extension"] == "tgz"
        assert record["installer-name"] == "whitaker-installer"
        assert record["expected-sha"] == _PINNED
        assert record["trust-anchor"] == "pinned"

    def test_uses_a_supplied_digest_for_an_unpinned_asset(
        self,
        tmp_path: Path,
    ) -> None:
        """Verify the input anchors an asset the manifest omits."""
        empty = tmp_path / "empty.sha256"
        empty.write_text("# none\n", encoding="utf-8")

        result = _run_resolution(
            tmp_path,
            WHITAKER_DIGEST_MANIFEST=str(empty),
            WHITAKER_INSTALLER_SHA256=_SUPPLIED,
        )

        record = _record(result)
        assert record["status"] == "install"
        assert record["expected-sha"] == _SUPPLIED
        assert record["trust-anchor"] == "input"

    @pytest.mark.parametrize(
        ("overrides", "expected_kind"),
        [
            pytest.param(
                {"RUNNER_ARCHITECTURE": "ARM32"},
                "unsupported-runner",
                id="unsupported-runner",
            ),
            pytest.param(
                {"WHITAKER_INSTALLER_SHA256": _SUPPLIED},
                "digest-conflict",
                id="digest-conflict",
            ),
        ],
    )
    def test_reports_a_failure_as_a_record(
        self,
        tmp_path: Path,
        overrides: dict[str, str],
        expected_kind: str,
    ) -> None:
        """Verify an expected failure is printed, not raised."""
        result = _run_resolution(tmp_path, **overrides)

        assert result.returncode == 0, result.stderr
        record = _record(result)
        assert record["status"] == "error"
        assert record["error-kind"] == expected_kind
        assert record["error-message"]

    def test_reports_an_unpinned_asset_as_a_record(self, tmp_path: Path) -> None:
        """Verify an asset with neither anchor is an error record."""
        empty = tmp_path / "empty.sha256"
        empty.write_text("# none\n", encoding="utf-8")

        result = _run_resolution(tmp_path, WHITAKER_DIGEST_MANIFEST=str(empty))

        assert result.returncode == 0, result.stderr
        record = _record(result)
        assert record["error-kind"] == "unpinned-digest"


class TestCachedInstaller:
    """Check how a cached installer and its version marker are treated."""

    def test_reuses_an_installer_recorded_for_this_version(
        self,
        tmp_path: Path,
    ) -> None:
        """Verify a matching marker short-circuits resolution."""
        _seed_cached_installer(tmp_path, _VERSION)

        record = _record(_run_resolution(tmp_path))

        assert record == {"status": "cached"}

    @pytest.mark.parametrize(
        ("marker", "expected_stale"),
        [
            pytest.param("0.2.6", "0.2.6", id="older-version"),
            pytest.param(None, "unknown", id="no-marker"),
        ],
    )
    def test_replaces_an_installer_from_another_version(
        self,
        tmp_path: Path,
        marker: str | None,
        expected_stale: str,
    ) -> None:
        """Verify a stale or missing marker resolves a fresh install."""
        _seed_cached_installer(tmp_path, marker)

        record = _record(_run_resolution(tmp_path))

        assert record["stale-version"] == expected_stale
        assert record["status"] == "install"
