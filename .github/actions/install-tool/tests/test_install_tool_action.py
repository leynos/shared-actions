"""Contract tests for the `install-tool` action manifest.

The order of the steps is the design rather than an accident, and so are the
guards: resolution is pure and comes first, the probe decides whether anything
is downloaded, and the installed binary is asked for its version last, on the
cached path as well as the fresh one. Each of those is asserted here, because
a reordering would still run and would still look like it worked.
"""

from __future__ import annotations

import pytest
from _manifest import METRICS, STEP_NAMES, action_steps, load_action, step_by_name

GUARDED_STEPS = ("Download and verify the archive", "Extract and install the binary")
CACHE_GUARD = "${{ steps.probe.outputs.needs-install == 'true' }}"


class TestInputs:
    """The whole input surface, compared at once."""

    def test_the_inputs_are_exactly_these(self) -> None:
        """A new input is a new promise, so it should not arrive unnoticed."""
        inputs = load_action()["inputs"]

        assert set(inputs) == {"tool", "version", "bin-dir"}
        assert inputs["tool"]["required"] is True
        assert inputs["version"]["required"] is True
        assert inputs["bin-dir"]["required"] is False
        assert inputs["bin-dir"]["default"] == ""

    def test_the_version_input_forbids_floating(self) -> None:
        """The description is the contract a caller reads before the code."""
        description = load_action()["inputs"]["version"]["description"]

        assert "latest" in description
        assert "no floating version" in description.replace("There is ", "")

    def test_the_outputs_are_exactly_these(self) -> None:
        """Callers depend on these names; the set is part of the interface."""
        assert set(load_action()["outputs"]) == {"path", "version", "cache-hit"}


class TestOrdering:
    """Positions carry the meaning here."""

    def test_the_steps_are_in_this_order(self) -> None:
        """Resolve, probe, download, install, verify. Nothing else, no gaps."""
        assert tuple(step.get("name") for step in action_steps()) == STEP_NAMES

    @pytest.mark.parametrize("name", GUARDED_STEPS)
    def test_the_costly_steps_are_guarded_by_the_probe(self, name: str) -> None:
        """A cached call must not download, or the probe buys nothing.

        The whole guard is compared rather than a substring, so a future `||`
        cannot widen it into running on a hit.
        """
        assert step_by_name(name)["if"] == CACHE_GUARD

    def test_the_verification_is_not_guarded(self) -> None:
        """A cached binary is still asked its version.

        A hit means a file of the right name was already there. Skipping the
        check on that path would trust the probe's own conclusion, which is
        the one thing that could be wrong.
        """
        assert "if" not in step_by_name("Verify the installed tool")

    def test_the_probe_is_not_guarded(self) -> None:
        """Everything after it reads its outputs, so it always runs."""
        assert "if" not in step_by_name("Probe for an installed tool")


class TestFragments:
    """Properties of the shell the action ships."""

    @pytest.mark.parametrize("name", STEP_NAMES)
    def test_every_step_is_a_run_step(self, name: str) -> None:
        """An action step here would be an action step's problem to have."""
        step = step_by_name(name)

        assert "uses" not in step
        assert step["shell"] == "bash"
        assert isinstance(step.get("run"), str)

    @pytest.mark.parametrize("name", STEP_NAMES)
    def test_every_fragment_sets_the_shell_options(self, name: str) -> None:
        """`set -euo pipefail`, and no `-E`, since no step declares a trap."""
        script = step_by_name(name)["run"]

        assert script.lstrip().startswith("set -euo pipefail")
        assert "set -Eeuo" not in script

    @pytest.mark.parametrize("name", STEP_NAMES)
    def test_no_fragment_contains_an_expression(self, name: str) -> None:
        """Values reach a fragment through `env:`, never by interpolation.

        A `${{ }}` inside the body is substituted before bash sees it, so a
        value containing a quote or a newline becomes shell. Keeping the body
        expression-free means it can be read, and tested, as shell.
        """
        assert "${{" not in step_by_name(name)["run"]

    @pytest.mark.parametrize("name", STEP_NAMES)
    def test_no_fragment_relies_on_an_err_trap(self, name: str) -> None:
        """An explicit `exit` fires no ERR trap.

        Reporting through one would silently lose the outcome of every path
        that exits deliberately, which here is most of them.
        """
        commands = [
            line.strip()
            for line in step_by_name(name)["run"].splitlines()
            if not line.strip().startswith("#")
        ]

        assert not [line for line in commands if line.startswith("trap ")]

    @pytest.mark.parametrize("name", STEP_NAMES)
    def test_no_fragment_uses_bash_4_syntax(self, name: str) -> None:
        """Bash 3.2 is what macOS runners ship.

        `[[ -v NAME ]]` needs 4.2 and `declare -A` needs 4.0, so both are out;
        `${NAME+x}` is the portable way to ask whether a variable is set.
        """
        script = step_by_name(name)["run"]

        assert "[[ -v " not in script
        assert "declare -A" not in script
        assert "${!" not in script


class TestTransport:
    """How the archive is fetched, asserted here because the tests stub it.

    The behavioural tests replace `curl` so they can serve a local file, which
    means the real invocation's guards are exercised nowhere else.
    """

    def test_the_download_refuses_anything_but_https(self) -> None:
        """A manifest URL is repository data, but the transport is not.

        `--proto '=https'` is what stops a redirect, or a mistyped entry,
        fetching over plain HTTP from something that can change underneath.
        """
        script = step_by_name("Download and verify the archive")["run"]

        assert "--proto '=https'" in script
        assert "--tlsv1.2" in script

    def test_the_download_is_bounded_and_retried(self) -> None:
        """A hung fetch should fail the job, not hold a runner for its timeout."""
        script = step_by_name("Download and verify the archive")["run"]

        assert "--max-time" in script
        assert "--connect-timeout" in script
        assert "--retry" in script

    def test_the_digest_is_computed_from_stdin(self) -> None:
        """GNU sha256sum escapes its output line for a name with a backslash.

        Hashing by file name therefore produced a leading backslash on
        Windows and rejected a correct archive. Feeding the file on stdin
        leaves no name to escape.
        """
        script = step_by_name("Download and verify the archive")["run"]

        assert 'sha256sum < "$archive"' in script
        assert 'shasum -a 256 < "$archive"' in script


class TestMetrics:
    """Every terminal path reports one bounded outcome."""

    def test_every_emitted_metric_is_declared(self) -> None:
        """A name the tests do not know about is an unbounded series.

        The action's fragments are scanned for the literal metric lines they
        emit, so a new one has to be added here, where its value set lives.
        """
        emitted = set()
        for name in STEP_NAMES:
            for line in step_by_name(name)["run"].splitlines():
                stripped = line.strip()
                if stripped.startswith('emit_metric "install-tool.'):
                    metric = stripped.split('"', 2)[1]
                    emitted.add(metric.split("=", 1)[0])

        assert emitted <= set(METRICS), sorted(emitted - set(METRICS))
        assert emitted, "no metrics found; has emit_metric been renamed?"

    def test_every_literal_value_is_inside_its_closed_set(self) -> None:
        """A literal outside the set widens the series without failing."""
        for name in STEP_NAMES:
            for line in step_by_name(name)["run"].splitlines():
                stripped = line.strip()
                if not stripped.startswith('emit_metric "install-tool.'):
                    continue
                metric, _, value = stripped.split('"', 2)[1].partition("=")
                if value.startswith("$"):
                    continue
                assert value in METRICS[metric], f"{metric}={value}"

    def test_the_metric_reaches_the_log_and_the_summary(self) -> None:
        """One audience reads the log and another aggregates the summary."""
        script = step_by_name("Resolve the tool entry")["run"]

        assert 'echo "metric $1"' in script
        assert "GITHUB_STEP_SUMMARY" in script
