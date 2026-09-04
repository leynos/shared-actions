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
    BINSTALL_FAILURE_STEP_NAME,
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

        assert list(inputs) == ["version", "binstall-version", "bin-dir"], (
            f"the input table changed: {list(inputs)}"
        )
        assert inputs["version"]["required"] is True, "version must be required"
        assert "default" not in inputs["version"], (
            "a required version must have no default to fall back to"
        )
        assert inputs["binstall-version"]["required"] is False, (
            "binstall-version must stay optional"
        )
        assert inputs["binstall-version"]["default"] == BINSTALL_ACTION_VERSION, (
            "the default binstall-version must match the pinned release"
        )
        assert inputs["bin-dir"]["required"] is False, "bin-dir must stay optional"
        assert inputs["bin-dir"]["default"] == "~/.local/bin", (
            f"the documented bin-dir default changed: {inputs['bin-dir']}"
        )

    def test_declares_no_outputs(self) -> None:
        """Verify the action exposes no outputs, only a PATH entry."""
        assert "outputs" not in load_manifest(), (
            "the action documents no outputs; adding one is a contract change"
        )


class TestStepOrdering:
    """Validate the composite step sequence."""

    def test_declares_the_documented_steps_in_order(self) -> None:
        """Verify the step names and their order."""
        names = [step["name"] for step in manifest_steps()]

        assert names == list(STEP_NAMES), f"the step sequence changed: {names}"

    def test_rejects_the_platform_before_consulting_the_cache(self) -> None:
        """Verify the platform gate precedes the cache probe.

        A cached executable must not be able to report success on a platform
        for which no prebuilt release exists.
        """
        names = [step["name"] for step in manifest_steps()]

        assert names.index("Check mdtablefix platform support") < names.index(
            "Probe mdtablefix and cargo-binstall",
        ), f"the platform gate must precede the cache probe: {names}"

    def test_every_run_step_uses_bash(self) -> None:
        """Verify each run-bearing step declares an inline Bash fragment."""
        for step in manifest_steps():
            if "uses" in step:
                continue
            assert step["shell"] == "bash", f"{step['name']!r} does not use bash"
            assert isinstance(step["run"], str), (
                f"{step['name']!r} declares no inline fragment"
            )


class TestBinstallProvisioning:
    """Validate how cargo-binstall is probed and, if absent, installed."""

    def test_probes_by_running_binstall_rather_than_by_presence(self) -> None:
        """Verify the probe runs ``cargo binstall -V``.

        A bare ``command -v`` reports a shim that cannot run, which is the
        presence-probe defect recorded in issue #420.
        """
        script = step_script("Probe mdtablefix and cargo-binstall")

        assert "cargo binstall -V" in script, (
            "the probe must run cargo-binstall rather than look for its name"
        )
        assert "command -v cargo-binstall" not in script, (
            "a presence probe reports an unusable shim as available"
        )

    def test_pins_the_upstream_installer_by_commit_sha(self) -> None:
        """Verify the upstream action is pinned by SHA, not by a movable tag."""
        step = step_by_name(BINSTALL_STEP_NAME)
        repository, _, reference = BINSTALL_ACTION_REF.partition("@")

        assert step["uses"] == BINSTALL_ACTION_REF, (
            f"the upstream reference changed: {step['uses']}"
        )
        assert repository == "cargo-bins/cargo-binstall", (
            f"unexpected upstream repository: {repository}"
        )
        assert len(reference) == 40, f"{reference} is not a full commit SHA"
        assert set(reference) <= set(string.hexdigits.lower()), (
            f"{reference} is not hexadecimal, so it is a tag, not a SHA"
        )

    def test_records_the_release_the_pinned_sha_tags(self) -> None:
        """Verify the comment names the release, so a bump is reviewable."""
        raw = ACTION_PATH.read_text(encoding="utf-8")

        assert f"{BINSTALL_ACTION_REF} # v{BINSTALL_ACTION_VERSION}" in raw, (
            "the pinned SHA must carry a comment naming its release"
        )

    def test_forwards_the_requested_binstall_version(self) -> None:
        """Verify the pinned installer receives the validated input."""
        step = step_by_name(BINSTALL_STEP_NAME)

        assert step["with"] == {
            "version": "${{ steps.validate-inputs.outputs.binstall-version }}",
        }, f"the upstream installer receives {step.get('with')}"

    def test_installs_binstall_only_when_the_probe_asked_for_it(self) -> None:
        """Verify the upstream step is conditioned on the probe's output."""
        step = step_by_name(BINSTALL_STEP_NAME)

        assert step["if"] == "${{ steps.probe.outputs.install-binstall == 'true' }}", (
            f"the upstream step must follow the probe: {step.get('if')}"
        )

    def test_annotates_a_failed_provisioning(self) -> None:
        """Verify a failed upstream installer still reports one outcome.

        The upstream step is the only one whose failure this action cannot
        annotate from inside, so a guarded step immediately after it names the
        outcome. Without it a bad ``binstall-version`` would stop the composite
        with no ``install-mdtablefix.result`` line at all.
        """
        step = step_by_name(BINSTALL_FAILURE_STEP_NAME)
        names = [item["name"] for item in manifest_steps()]

        assert step["if"] == (
            "${{ failure() && steps.probe.outputs.install-binstall == 'true' }}"
        ), "the reporting step must be guarded by both failure and the probe"
        assert names.index(BINSTALL_FAILURE_STEP_NAME) == (
            names.index(BINSTALL_STEP_NAME) + 1
        ), "the reporting step must immediately follow the upstream installer"
        script = step_script(BINSTALL_FAILURE_STEP_NAME)
        assert "install-mdtablefix.result=binstall-unavailable" in script, (
            "the reporting step must emit a bounded result metric"
        )
        assert "::error title=Install mdtablefix failed::" in script, (
            "the reporting step must annotate the failure"
        )


class TestInstallStep:
    """Validate the hardened cargo-binstall invocation."""

    def test_runs_only_when_the_probe_found_no_cached_executable(self) -> None:
        """Verify the install step is conditioned on the probe's output."""
        step = step_by_name("Install mdtablefix")

        assert step["if"] == "${{ steps.probe.outputs.needs-install == 'true' }}", (
            f"the install step must respect the cache probe: {step.get('if')}"
        )

    def test_disables_the_compile_strategy(self) -> None:
        """Verify a missing prebuilt asset fails closed instead of compiling."""
        script = step_script("Install mdtablefix")

        assert "--disable-strategies compile" in script, (
            "the compile strategy must be disabled so a missing asset fails closed"
        )
        assert "cargo install" not in script, (
            "a cargo install fallback would build from source in CI"
        )

    def test_passes_the_hardening_flags(self) -> None:
        """Verify the non-interactive, locked, telemetry-free invocation."""
        script = step_script("Install mdtablefix")

        for flag in ("--no-confirm", "--locked", "--disable-telemetry"):
            assert flag in script, f"the install step no longer passes {flag}"

    def test_overrides_the_broken_bin_dir_metadata(self) -> None:
        """Verify the mdtablefix 0.5.0 workaround is present and explained.

        mdtablefix 0.5.0 declares ``bin-dir = "."``, which cargo-binstall 1.22
        rejects. Remove this assertion together with the override once a pinned
        release carries fixed metadata (leynos/mdtablefix#458).
        """
        script = step_script("Install mdtablefix")

        assert f"--bin-dir '{BIN_DIR_OVERRIDE}'" in script, (
            "the bin-dir override is required while mdtablefix 0.5.0 is pinned"
        )
        assert "leynos/mdtablefix#458" in script, (
            "the override must cite the upstream issue that justifies it"
        )

    def test_installs_into_the_validated_bin_dir(self) -> None:
        """Verify the executable lands where the caller's cache owns it."""
        script = step_script("Install mdtablefix")

        assert '--install-path "$MDTABLEFIX_BIN_DIR"' in script, (
            "the executable must land in the caller-owned bin-dir"
        )
        assert step_env("Install mdtablefix")["MDTABLEFIX_BIN_DIR"] == (
            "${{ steps.validate-inputs.outputs.bin-dir }}"
        ), "the install step must use the validated bin-dir, not the raw input"


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
