"""Native probes: what a build or import probe proves, and what it must not.

A probe is evidence about the package under the project interpreter, so it is
skipped when that interpreter was refused. A failing probe is attributed to the
package and quotes its first diagnostic, which is where a build stops.
"""

from __future__ import annotations

import sys
import typing as typ

from workflow_scripts.tests.mutation_preflight_support import (
    RULE_AVAILABLE,
    Checkout,
    Outcome,
    locked_checkout,
    outcome_for,
)

if typ.TYPE_CHECKING:
    from pathlib import Path


class TestNativeProbe:
    """A probe is run only where its result means something."""

    @RULE_AVAILABLE
    def test_a_passing_probe_is_recorded_with_its_exit_code(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A probe that exits zero is recorded as the evidence it produced."""
        manifest = (
            "[native]\n"
            'package = "tei-rapporteur"\n'
            'supported_python = ">=3.12"\n'
            f"probe = ['{sys.executable}', '-c', 'print(1)']\n"
        )

        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            locked_checkout(manifest),
            project_python="3.13",
        )

        assert outcome.exit_code == 0, outcome.stdout
        probe = typ.cast("dict[str, object]", outcome.detail("native")["probe"])
        assert probe["exit_code"] == 0, "the probe's exit status must be recorded"

    @RULE_AVAILABLE
    def test_a_probe_is_skipped_when_the_interpreter_was_rejected(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A probe would sync the project under the interpreter just refused."""
        marker = tmp_path / "probe-ran"
        script = f'import pathlib; pathlib.Path("{marker.as_posix()}").write_text("")'
        manifest = (
            "[native]\n"
            'package = "tei-rapporteur"\n'
            'supported_python = ">=3.12"\n'
            f"probe = ['{sys.executable}', '-c', '{script}']\n"
            "[[checks]]\n"
            'name = "collect"\n'
            f"command = ['{sys.executable}', '-c', 'print(1)']\n"
        )

        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            Checkout(manifest=manifest, requires_python=">=3.14"),
            project_python="3.13",
        )

        assert not marker.exists(), "the probe ran under a rejected interpreter"
        assert outcome.phases["native"]["status"] == "unknown"
        assert "'s probe was not run" in outcome.findings("native")
        assert outcome.phases["checks"]["status"] == "skipped"
        assert "the declared checks were not run" in str(
            outcome.detail("checks")["reason"]
        )

    def test_a_failing_probe_reports_the_package_and_first_diagnostic(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A build or import probe failure is attributed to the package."""
        script = 'raise ImportError("no module named tei")'
        manifest = (
            "[native]\n"
            'package = "tei-rapporteur"\n'
            'supported_python = ">=3.12"\n'
            f"probe = ['{sys.executable}', '-c', '{script}']\n"
        )

        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            locked_checkout(manifest),
            project_python="3.13",
        )

        assert outcome.exit_code == 1
        found = outcome.findings("native")
        assert "the native probe for tei-rapporteur failed" in found
        assert "no module named tei" in found

    def test_the_first_diagnostic_is_the_one_reported(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A build stops at its first error, so the last line is the least useful."""
        script = (
            'import sys; sys.stderr.write("\\n".join('
            '["error: first", "  note: indented", "warning: last"])); sys.exit(1)'
        )
        manifest = (
            "[native]\n"
            'package = "tei-rapporteur"\n'
            'supported_python = ">=3.12"\n'
            f"probe = ['{sys.executable}', '-c', '{script}']\n"
        )

        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            locked_checkout(manifest),
            project_python="3.13",
        )

        assert outcome.exit_code == 1
        found = outcome.findings("native")
        assert "failed with exit code 1: error: first" in found
        assert "warning: last" not in found
