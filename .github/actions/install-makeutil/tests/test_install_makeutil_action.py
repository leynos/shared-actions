"""Contract tests for the `install-makeutil` action manifest.

These assert the commands the manifest runs, not merely that a step with a
plausible name exists: a step could be renamed to match `STEP_NAMES` while
its `run:` body silently drifted, and a Cargo call could be reintroduced
without any of this action's own tests noticing unless the whole `run:` body
is inspected. See "Workflow contracts must assert commands, not identifiers"
in this repository's operating notes.
"""

from __future__ import annotations

import re

from _makeutil_action import (
    CACHE_ACTION_REF,
    INSTALL_SCRIPT_PATH,
    METRIC_RESULTS,
    STEP_NAMES,
    action_steps,
    load_action,
    step_by_name,
)
from install_makeutil import _TARGETS
from makeutil_verify import CACHE_HIT, CACHE_MISS, CACHE_STALE


class TestInputsAndOutputs:
    """The whole input/output surface, compared at once."""

    def test_the_inputs_are_exactly_these(self) -> None:
        """A new input is a new promise, so it should not arrive unnoticed."""
        inputs = load_action()["inputs"]

        assert set(inputs) == {"version", "bin-dir", "expected-sha256-override"}
        assert inputs["version"]["default"] == "0.1.0"
        assert inputs["bin-dir"]["default"] == "~/.local/bin"
        assert inputs["expected-sha256-override"]["default"] == ""
        assert inputs["expected-sha256-override"]["required"] is False

    def test_the_override_input_documents_itself_as_test_only(self) -> None:
        """A caller reading the description must see this is not for them."""
        description = load_action()["inputs"]["expected-sha256-override"]["description"]

        assert "Test-only" in description or "test-only" in description

    def test_the_outputs_are_exactly_these(self) -> None:
        """Callers depend on these names; the set is part of the interface."""
        outputs = load_action()["outputs"]

        assert set(outputs) == {"path", "version", "result"}
        assert outputs["path"]["value"] == "${{ steps.install.outputs.path }}"
        assert outputs["version"]["value"] == "${{ steps.install.outputs.version }}"
        assert outputs["result"]["value"] == "${{ steps.install.outputs.result }}"


class TestOrdering:
    """Positions carry the meaning here: resolve, cache, install, PATH."""

    def test_the_steps_are_in_this_order(self) -> None:
        """Resolution is pure and first; the cache sits before the install
        it guards; PATH is extended only once installation has succeeded.
        """
        assert tuple(step.get("name") for step in action_steps()) == STEP_NAMES


class TestCacheStep:
    """The action owns its own cache, unlike install-mdtablefix."""

    def test_the_cache_action_is_pinned_by_the_repositorys_existing_sha(
        self,
    ) -> None:
        """The same `actions/cache` pin used elsewhere in this repository,
        so a bump happens once rather than drifting action by action.
        """
        cache_step = step_by_name("Restore cached makeutil")

        assert cache_step["uses"] == CACHE_ACTION_REF

    def test_the_cache_is_keyed_on_the_resolved_plan(self) -> None:
        """The key and path both come from the resolve step's outputs."""
        cache_step = step_by_name("Restore cached makeutil")

        assert cache_step["with"]["key"] == "${{ steps.resolve.outputs.cache-key }}"
        assert (
            cache_step["with"]["path"] == "${{ steps.resolve.outputs.executable-path }}"
        )


class TestScriptInvocations:
    """Each Python-calling step runs exactly the expected command."""

    def test_the_resolve_step_runs_exactly_this_command(self) -> None:
        """Every resolved value is threaded through `env:`, not interpolated
        into the command line, and the subcommand and flags are exact.
        """
        step = step_by_name("Resolve the makeutil install plan")

        assert step["shell"] == "bash"
        assert "${{" not in step["run"]
        expected_command = (
            'python3 "$RESOLVE_SCRIPT" resolve \\\n'
            '  --version "$VERSION_INPUT" \\\n'
            '  --bin-dir "$BIN_DIR_INPUT" \\\n'
            '  --sha256-override "$SHA256_OVERRIDE_INPUT" \\\n'
            '  --runner-os "$RUNNER_OPERATING_SYSTEM" \\\n'
            '  --runner-arch "$RUNNER_ARCHITECTURE"'
        )
        assert expected_command in step["run"]
        assert step["env"]["RESOLVE_SCRIPT"] == (
            "${{ github.action_path }}/scripts/install_makeutil.py"
        )

    def test_the_install_step_runs_exactly_this_command(self) -> None:
        """Same discipline: exact flags, values only ever reached through
        `env:`.
        """
        step = step_by_name("Install makeutil")

        assert step["shell"] == "bash"
        assert "${{" not in step["run"]
        expected_command = (
            'python3 "$INSTALL_SCRIPT" install \\\n'
            '  --executable-path "$EXECUTABLE_PATH" \\\n'
            '  --expected-sha256 "$EXPECTED_SHA256" \\\n'
            '  --binary-url "$BINARY_URL" \\\n'
            '  --sidecar-url "$SIDECAR_URL" \\\n'
            '  --version "$VERSION"'
        )
        assert expected_command in step["run"]

    def test_the_path_step_runs_exactly_this_command(self) -> None:
        """The final step's whole body is one line: append and nothing else."""
        step = step_by_name("Add makeutil to PATH")

        assert step["run"].strip() == 'echo "$BIN_DIR" >> "$GITHUB_PATH"'

    def test_no_step_invokes_cargo(self) -> None:
        """No step calls `cargo`, because the action never builds from source.

        A `cargo` call anywhere in it would be exactly that regression.
        """
        for name in STEP_NAMES:
            step = step_by_name(name)
            run_body = step.get("run", "")
            assert not re.search(r"\bcargo\b", run_body)
            assert step.get("uses", "").split("@")[0] != "cargo-bins/cargo-binstall"


class TestMetrics:
    """Every terminal path reports one bounded, documented outcome."""

    def test_the_declared_metric_vocabulary_matches_the_readme(self) -> None:
        """The README table and the constant here must not drift apart."""
        readme = (INSTALL_SCRIPT_PATH.parents[1] / "README.md").read_text(
            encoding="utf-8"
        )

        for result in METRIC_RESULTS:
            assert f"install-makeutil.result={result}" in readme

    def test_the_cache_states_match_the_readme(self) -> None:
        """Each cache state the script can report is documented."""
        readme = (INSTALL_SCRIPT_PATH.parents[1] / "README.md").read_text(
            encoding="utf-8"
        )

        for state in (CACHE_HIT, CACHE_MISS, CACHE_STALE):
            assert f"install-makeutil.cache={state}" in readme

    def test_the_install_step_passes_the_cache_hit_output(self) -> None:
        """Without `cache-hit`, a rejected restore would read as a plain miss."""
        step = step_by_name("Install makeutil")

        assert "steps.cache.outputs.cache-hit" in step["env"]["CACHE_HIT"]
        assert '--cache-hit "$CACHE_HIT"' in step["run"]

    def test_the_digest_table_module_defines_only_these_targets(self) -> None:
        """Reading the module's own table, not a restatement of it, is what
        makes this a contract rather than a second copy that can drift.
        """
        assert set(_TARGETS) == {("Linux", "X64"), ("Linux", "ARM64")}
