"""Contracts for the action's Clippy-snapshot bootstrap steps.

The behaviour of each step is exercised in the fresh-runner lanes
(`.github/workflows/test-install-whitaker-bootstrap.yml`); these tests hold the
shape that makes those lanes mean something: the order of the steps, the cache
identity, the bounded wipe, the fail-closed download, and the absence of any
route by which the bootstrap configuration could leak into other steps.
"""

from __future__ import annotations

import re

import pytest
from _action_manifest import ACTION_PATH, manifest_steps, step_by_name


def _script(name: str) -> str:
    run = step_by_name(name)["run"]
    assert isinstance(run, str)
    return run


class TestBootstrapContract:
    """The bootstrap steps' wiring."""

    def test_the_cache_key_carries_the_whole_identity(self) -> None:
        """Version, Dylint release, Clippy commit and toolchain all key the cache."""
        key = " ".join(
            str(step_by_name("Cache Whitaker bootstrap")["with"]["key"]).split()
        )
        for part in (
            "runner.os",
            "runner.arch",
            "steps.validate-inputs.outputs.installer-version",
            "steps.snapshot.outputs.dylint-version",
            "steps.snapshot.outputs.commit",
            "steps.snapshot.outputs.toolchain",
            "steps.validate-inputs.outputs.cache-salt",
        ):
            assert part in key, f"the bootstrap cache key omits {part}"

    def test_the_bootstrap_cache_is_separate_from_the_installer_cache(self) -> None:
        """Wiping the bootstrap can never touch the installer cache."""
        installer = str(step_by_name("Cache Whitaker installer")["with"]["path"])
        bootstrap = str(step_by_name("Cache Whitaker bootstrap")["with"]["path"])
        assert "bootstrap-location" in bootstrap
        assert "bootstrap-location" not in installer

    def test_a_mismatched_cache_wipes_only_the_bootstrap_root_and_says_why(
        self,
    ) -> None:
        """One `rm -rf`, on the bootstrap root, after a logged reason."""
        script = _script("Verify Whitaker bootstrap cache")
        removals = re.findall(r"rm -rf -- (.+)", script)
        assert removals == ['"$root"'], removals
        assert script.index("::warning") < script.index("rm -rf")
        for check in (
            "bootstrap.json",
            "snapshot_commit",
            "dylint_version",
            "toolchain",
        ):
            assert check in script

    def test_the_wipe_is_refused_for_any_root_but_the_bootstrap_directory(
        self,
    ) -> None:
        """A guard on the root's name precedes the removal."""
        script = _script("Verify Whitaker bootstrap cache")
        assert "whitaker-bootstrap) ;;" in script
        assert "${root##*/}" in script
        assert script.index("${root##*/}") < script.index("rm -rf")

    def test_the_derived_paths_are_checked_against_the_installers_report(
        self,
    ) -> None:
        """The final step compares generation and driver with the dry run."""
        script = _script("Verify Whitaker bootstrap")
        for compared in (
            '== "$(normalise "$generation")" ]]',
            "$(field driver-directory)",
        ):
            assert compared in script
        assert "--dry-run --skip-deps" in script
        assert "bootstrap-paths.sh" in "".join(
            str(v) for v in step_by_name("Verify Whitaker bootstrap")["env"].values()
        )

    def test_the_snapshot_download_is_verified_twice_and_has_no_fallback(self) -> None:
        """Pinned digest and release sidecar both gate the bundle."""
        script = _script("Download Whitaker Clippy snapshot")
        assert "digest mismatch" in script
        assert "sidecar digest" in script
        assert 'if [[ "$sidecar" != "$actual" ]]; then' in script
        assert 'if [[ "$actual" != "$WHITAKER_EXPECTED_SHA" ]]; then' in script
        assert "https://github.com/leynos/whitaker/releases/download/" in script
        assert "rust-clippy" not in script, "the action must never name public Clippy"

    def test_the_run_step_hands_the_verified_bundle_to_the_installer(self) -> None:
        """The bundle reaches the installer by flag, from the download step."""
        script = _script("Run Whitaker installer")
        assert '--clippy-snapshot-bundle "$WHITAKER_SNAPSHOT_BUNDLE"' in script
        env = step_by_name("Run Whitaker installer")["env"]
        assert env["WHITAKER_SNAPSHOT_BUNDLE"] == (
            "${{ steps.snapshot-download.outputs.bundle }}"
        )

    def test_bootstrap_configuration_never_enters_the_job_environment(self) -> None:
        """The wrapper carries DYLINT_* per run; the action exports none of it."""
        text = ACTION_PATH.read_text(encoding="utf-8")
        assert "GITHUB_ENV" not in text, "the action must not write the job environment"

    def test_the_verification_runs_after_the_installer(self) -> None:
        """The proof of what was installed comes last."""
        names = [step["name"] for step in manifest_steps()]
        assert names[-1] == "Verify Whitaker bootstrap"
        assert names.index("Run Whitaker installer") < names.index(
            "Verify Whitaker bootstrap"
        )

    @pytest.mark.parametrize(
        "name",
        [
            "Resolve Whitaker Clippy snapshot",
            "Locate Whitaker bootstrap",
            "Verify Whitaker bootstrap cache",
            "Download Whitaker Clippy snapshot",
            "Verify Whitaker bootstrap",
        ],
    )
    def test_each_bootstrap_step_is_skipped_for_an_installer_without_one(
        self, name: str
    ) -> None:
        """Steps after the resolution run only when it said `install`."""
        condition = str(step_by_name(name).get("if", ""))
        if name == "Resolve Whitaker Clippy snapshot":
            assert condition == ""
        else:
            assert "steps.snapshot.outputs.status == 'install'" in condition
