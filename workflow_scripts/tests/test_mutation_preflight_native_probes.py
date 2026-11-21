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
    lock_document,
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

    @RULE_AVAILABLE
    def test_a_probe_that_relocks_the_project_fails_the_phase(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A probe must not pass after moving the revision the lock records."""
        pinned = lock_document(
            "0.3.1", 'git = "https://example.invalid/tei.git?rev=abc123"'
        )
        relocked = lock_document(
            "0.3.1", 'git = "https://example.invalid/tei.git?rev=def456"'
        )
        # The probe stands in for a tool that relocks the project, such as
        # `uv run` without --locked: it succeeds, and leaves a different lock.
        script = 'import shutil; shutil.copyfile("relocked.lock", "uv.lock")'
        manifest = (
            "[native]\n"
            'package = "tei-rapporteur"\n'
            'revision = "abc123"\n'
            'supported_python = ">=3.12"\n'
            f"probe = ['{sys.executable}', '-c', '{script}']\n"
        )

        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            Checkout(
                manifest=manifest,
                extra={"uv.lock": pinned, "relocked.lock": relocked},
            ),
            project_python="3.13",
        )

        assert outcome.exit_code == 1, outcome.stdout
        found = outcome.findings("native")
        assert (
            "changed uv.lock from version '0.3.1', "
            "source 'git=https://example.invalid/tei.git?rev=abc123', "
            "revision 'abc123' to version '0.3.1', "
            "source 'git=https://example.invalid/tei.git?rev=def456', "
            "revision 'def456'" in found
        ), f"the finding must name the identity the probe stood between: {found}"

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


class TestChecksAndTheLock:
    """The native judgement must describe the lock the checks leave behind."""

    def test_a_check_that_relocks_the_project_fails_the_native_phase(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """The judgement runs after the checks, so a lock they moved is failed."""
        pinned = lock_document(
            "0.3.1", 'git = "https://example.invalid/tei.git?rev=abc123"'
        )
        relocked = lock_document(
            "0.3.1", 'git = "https://example.invalid/tei.git?rev=def456"'
        )
        # The check stands in for a fixture that relocks the project, such as
        # `uv run` without --locked: it succeeds, and leaves a different lock.
        script = 'import shutil; shutil.copyfile("relocked.lock", "uv.lock")'
        manifest = (
            "[native]\n"
            'package = "tei-rapporteur"\n'
            'revision = "abc123"\n'
            "[[checks]]\n"
            'name = "relock"\n'
            f"command = ['{sys.executable}', '-c', '{script}']\n"
        )

        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            Checkout(
                manifest=manifest,
                extra={"uv.lock": pinned, "relocked.lock": relocked},
            ),
            lane="cargo",
            project_python="3.13",
        )

        assert outcome.exit_code == 1, outcome.stdout
        assert outcome.phases["checks"]["status"] == "pass", (
            "the check itself succeeded; the phase that must fail is the judgement"
        )
        assert outcome.phases["native"]["status"] == "fail", (
            "a lock the checks moved must not be certified as the judged one"
        )
        found = outcome.findings("native")
        assert "a declared check changed uv.lock for tei-rapporteur" in found, (
            f"the finding must name the check that moved the lock: {found}"
        )
        assert "revision 'def456'" in found, (
            f"the finding must name the identity the lock now records: {found}"
        )
