"""Verify the install-mdtablefix action manifest's declared contract.

These tests read ``action.yml`` only. They assert the input table, the step
ordering, the pinned upstream cargo-binstall reference, and the flags the
install step passes, so a manifest change that breaks a caller shows up here
rather than in a workflow run. The executable behaviour lives in the sibling
input and install modules. Run the suite with ``uv run pytest
.github/actions/install-mdtablefix/tests``.
"""

from __future__ import annotations

import re
import string
import typing as typ

from _mdtablefix_manifest import (
    ACTION_PATH,
    BIN_DIR_OVERRIDE,
    BINSTALL_ACTION_REF,
    BINSTALL_ACTION_VERSION,
    BINSTALL_STEP_NAME,
    STEP_NAMES,
    load_manifest,
    manifest_steps,
    step_by_name,
    step_env,
    step_script,
)


class TestInputs:
    """Validate the action's declared input contract."""

    def test_declares_the_documented_inputs(self) -> None:
        """Verify every input, its requirement, and its default."""
        inputs = typ.cast("dict[str, dict[str, object]]", load_manifest()["inputs"])

        assert list(inputs) == ["version", "binstall-version", "bin-dir"]
        assert inputs["version"]["required"] is True
        assert "default" not in inputs["version"]
        assert inputs["binstall-version"]["required"] is False
        assert inputs["binstall-version"]["default"] == BINSTALL_ACTION_VERSION
        assert inputs["bin-dir"]["required"] is False
        assert inputs["bin-dir"]["default"] == "~/.local/bin"

    def test_declares_no_outputs(self) -> None:
        """Verify the action exposes no outputs, only a PATH entry."""
        assert "outputs" not in load_manifest()


class TestStepOrdering:
    """Validate the composite step sequence."""

    def test_declares_the_documented_steps_in_order(self) -> None:
        """Verify the step names and their order."""
        assert [step["name"] for step in manifest_steps()] == list(STEP_NAMES)

    def test_rejects_the_platform_before_consulting_the_cache(self) -> None:
        """Verify the platform gate precedes the cache probe.

        A cached executable must not be able to report success on a platform
        for which no prebuilt release exists.
        """
        names = [step["name"] for step in manifest_steps()]

        assert names.index("Check mdtablefix platform support") < names.index(
            "Probe mdtablefix and cargo-binstall",
        )

    def test_every_run_step_uses_bash(self) -> None:
        """Verify each run-bearing step declares an inline Bash fragment."""
        for step in manifest_steps():
            if "uses" in step:
                continue
            assert step["shell"] == "bash"
            assert isinstance(step["run"], str)


class TestBinstallProvisioning:
    """Validate how cargo-binstall is probed and, if absent, installed."""

    def test_probes_by_running_binstall_rather_than_by_presence(self) -> None:
        """Verify the probe runs ``cargo binstall -V``.

        A bare ``command -v`` reports a shim that cannot run, which is the
        presence-probe defect recorded in issue #420.
        """
        script = step_script("Probe mdtablefix and cargo-binstall")

        assert "cargo binstall -V" in script
        assert "command -v cargo-binstall" not in script

    def test_pins_the_upstream_installer_by_commit_sha(self) -> None:
        """Verify the upstream action is pinned by SHA, not by a movable tag."""
        step = step_by_name(BINSTALL_STEP_NAME)
        repository, _, reference = BINSTALL_ACTION_REF.partition("@")

        assert step["uses"] == BINSTALL_ACTION_REF
        assert repository == "cargo-bins/cargo-binstall"
        assert len(reference) == 40
        assert set(reference) <= set(string.hexdigits.lower())

    def test_records_the_release_the_pinned_sha_tags(self) -> None:
        """Verify the comment names the release, so a bump is reviewable."""
        raw = ACTION_PATH.read_text(encoding="utf-8")

        assert f"{BINSTALL_ACTION_REF} # v{BINSTALL_ACTION_VERSION}" in raw

    def test_forwards_the_requested_binstall_version(self) -> None:
        """Verify the pinned installer receives the validated input."""
        step = step_by_name(BINSTALL_STEP_NAME)

        assert step["with"] == {
            "version": "${{ steps.validate-inputs.outputs.binstall-version }}",
        }

    def test_installs_binstall_only_when_the_probe_asked_for_it(self) -> None:
        """Verify the upstream step is conditioned on the probe's output."""
        step = step_by_name(BINSTALL_STEP_NAME)

        assert step["if"] == "${{ steps.probe.outputs.install-binstall == 'true' }}"


class TestInstallStep:
    """Validate the hardened cargo-binstall invocation."""

    def test_runs_only_when_the_probe_found_no_cached_executable(self) -> None:
        """Verify the install step is conditioned on the probe's output."""
        step = step_by_name("Install mdtablefix")

        assert step["if"] == "${{ steps.probe.outputs.needs-install == 'true' }}"

    def test_disables_the_compile_strategy(self) -> None:
        """Verify a missing prebuilt asset fails closed instead of compiling."""
        script = step_script("Install mdtablefix")

        assert "--disable-strategies compile" in script
        assert "cargo install" not in script

    def test_passes_the_hardening_flags(self) -> None:
        """Verify the non-interactive, locked, telemetry-free invocation."""
        script = step_script("Install mdtablefix")

        for flag in ("--no-confirm", "--locked", "--disable-telemetry"):
            assert flag in script

    def test_overrides_the_broken_bin_dir_metadata(self) -> None:
        """Verify the mdtablefix 0.5.0 workaround is present and explained.

        mdtablefix 0.5.0 declares ``bin-dir = "."``, which cargo-binstall 1.22
        rejects. Remove this assertion together with the override once a pinned
        release carries fixed metadata (leynos/mdtablefix#458).
        """
        script = step_script("Install mdtablefix")

        assert f"--bin-dir '{BIN_DIR_OVERRIDE}'" in script
        assert "leynos/mdtablefix#458" in script

    def test_installs_into_the_validated_bin_dir(self) -> None:
        """Verify the executable lands where the caller's cache owns it."""
        script = step_script("Install mdtablefix")

        assert '--install-path "$MDTABLEFIX_BIN_DIR"' in script
        assert step_env("Install mdtablefix")["MDTABLEFIX_BIN_DIR"] == (
            "${{ steps.validate-inputs.outputs.bin-dir }}"
        )


class TestBashCompatibility:
    """Guard the Bash 3.2 floor the macOS runner image imposes."""

    def test_uses_no_bash_four_only_constructs(self) -> None:
        """Verify no fragment relies on Bash 4 syntax.

        macOS runners ship Bash 3.2, which has no ``${var,,}`` case expansion,
        no ``mapfile``, no ``readarray``, and no associative arrays. Expanding
        an empty array under ``set -u`` is also an error there, so the
        fragments use none.
        """
        forbidden = (",,}", "^^}", "mapfile", "readarray", "declare -A", "&>>")
        for step in manifest_steps():
            script = step.get("run")
            if not isinstance(script, str):
                continue
            for token in forbidden:
                assert token not in script, (
                    f"{step['name']!r} uses the Bash 4 construct {token!r}"
                )

    def test_reports_failures_without_an_err_trap(self) -> None:
        """Verify no fragment reports a failure through an ``ERR`` trap.

        Bash 3.2 did not run the trap when cargo-binstall exited non-zero on a
        macOS runner: the step failed with no annotation and no metric. Every
        failure path checks an exit status explicitly instead.
        """
        declares_err_trap = re.compile(r"^\s*trap\b.*\bERR\b", re.MULTILINE)
        for step in manifest_steps():
            script = step.get("run")
            if not isinstance(script, str):
                continue
            assert declares_err_trap.search(script) is None, (
                f"{step['name']!r} declares an ERR trap"
            )
