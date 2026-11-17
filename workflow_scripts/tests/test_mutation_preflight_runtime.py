"""The mutation preflight's runtime phases: tools, checks, native support.

Each case is one of the failures issue #591 recorded: an absent mold or
mdtablefix, a check that must run from the boundary it declares, an
unsupported native tuple, and the envelope that must exist before any mutant
does.
"""

from __future__ import annotations

import json
import sys
import typing as typ

from workflow_scripts.tests.mutation_preflight_support import (
    POSIX_SHIMS_ONLY,
    Checkout,
    Outcome,
    lock_document,
    repository,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest


class TestCommands:
    """A required tool that is absent or wrong stops the run by name."""

    @POSIX_SHIMS_ONLY
    def test_a_missing_command_is_named(
        self,
        tmp_path: Path,
        run_preflight: typ.Callable[..., Outcome],
        empty_path: None,
    ) -> None:
        """An absent command is the failure, and it is named."""
        outcome = run_preflight(
            repository(
                tmp_path / "repo",
                Checkout(manifest='[[require_commands]]\nname = "mdtablefix"\n'),
            ),
            project_python="3.13",
        )

        assert outcome.exit_code == 1
        assert "required command 'mdtablefix'" in outcome.findings("commands")
        assert "PATH" in outcome.findings("commands")

    @POSIX_SHIMS_ONLY
    def test_a_present_command_passes(
        self,
        tmp_path: Path,
        run_preflight: typ.Callable[..., Outcome],
        install_tool: typ.Callable[..., None],
    ) -> None:
        """The installed tool lets the same declaration pass."""
        install_tool("mold", 'echo "mold 2.41.0 (compatible with GNU ld)"')

        outcome = run_preflight(
            repository(
                tmp_path / "repo",
                Checkout(
                    manifest='[[require_commands]]\nname = "mold"\nversion = "2.41"\n'
                ),
            ),
            project_python="3.13",
        )

        assert outcome.exit_code == 0, outcome.stdout
        assert outcome.phases["commands"]["status"] == "pass"

    @POSIX_SHIMS_ONLY
    def test_a_nearby_version_is_refused(
        self,
        tmp_path: Path,
        run_preflight: typ.Callable[..., Outcome],
        install_tool: typ.Callable[..., None],
    ) -> None:
        """``2.4`` must not accept ``2.41.0``, which is what a substring test does."""
        install_tool("mold", 'echo "mold 2.41.0"')

        outcome = run_preflight(
            repository(
                tmp_path / "repo",
                Checkout(
                    manifest='[[require_commands]]\nname = "mold"\nversion = "2.4"\n'
                ),
            ),
            project_python="3.13",
        )

        assert outcome.exit_code == 1
        assert "does not report '2.4'" in outcome.findings("commands")
        assert "2.41.0" in outcome.findings("commands")


class TestChecks:
    """Declared checks run from the boundary they declare."""

    def test_a_check_runs_from_its_declared_cwd_and_environment(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """cwd, import root and environment are the ones the declaration names."""
        script = (
            "import os, pathlib; "
            'print(pathlib.Path.cwd().name, os.environ.get("MARKER"), '
            'os.environ.get("PYTHONPATH"))'
        )
        manifest = (
            "[[checks]]\n"
            'name = "collect"\n'
            f"command = ['{sys.executable}', '-c', '{script}']\n"
            'cwd = "workflow_scripts/tests"\n'
            "timeout_seconds = 30\n"
            'env = { MARKER = "declared", PYTHONPATH = ".." }\n'
        )

        outcome = run_preflight(
            repository(tmp_path / "repo", Checkout(manifest=manifest)),
            project_python="3.13",
        )

        assert outcome.exit_code == 0, outcome.stdout
        printed = typ.cast("dict[str, object]", outcome.detail("checks")["collect"])
        assert printed["cwd"] == "workflow_scripts/tests"
        assert str(printed["stdout"]).split() == ["tests", "declared", ".."], (
            "the declared cwd, environment and import root must reach the command"
        )

    def test_a_failing_check_names_the_check_and_its_last_line(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A collection failure is reported as itself, not as a mutant failure."""
        script = 'import sys; sys.stderr.write("boom: no such option"); sys.exit(2)'
        manifest = (
            "[[checks]]\n"
            'name = "collect"\n'
            f"command = ['{sys.executable}', '-c', '{script}']\n"
        )

        outcome = run_preflight(
            repository(tmp_path / "repo", Checkout(manifest=manifest)),
            project_python="3.13",
        )

        assert outcome.exit_code == 1
        found = outcome.findings("checks")
        assert "check 'collect' failed with exit code 2" in found
        assert "boom: no such option" in found

    def test_a_check_keeps_bounded_logs(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """Even a passing check leaves its streams for the job's diagnostics."""
        script = 'print("collected 12 tests")'
        manifest = (
            "[[checks]]\n"
            'name = "collect"\n'
            f"command = ['{sys.executable}', '-c', '{script}']\n"
        )

        outcome = run_preflight(
            repository(tmp_path / "repo", Checkout(manifest=manifest)),
            project_python="3.13",
        )

        assert outcome.exit_code == 0, outcome.stdout
        log = tmp_path / "out" / "checks" / "collect.stdout.log"
        assert "collected 12 tests" in log.read_text(encoding="utf-8")

    def test_a_hung_check_is_bounded_by_its_timeout(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A check that never returns is a named failure, not a stuck job."""
        script = "import time; time.sleep(30)"
        manifest = (
            "[[checks]]\n"
            'name = "slow"\n'
            f"command = ['{sys.executable}', '-c', '{script}']\n"
            "timeout_seconds = 1\n"
        )

        outcome = run_preflight(
            repository(tmp_path / "repo", Checkout(manifest=manifest)),
            project_python="3.13",
        )

        assert outcome.exit_code == 1
        assert "did not finish within 1 seconds" in outcome.findings("checks")


class TestNative:
    """A locked native dependency is named with its support boundary."""

    def _lock(self, version: str, source: str) -> str:
        """Return a ``uv.lock`` pinning one package at *version* from *source*."""
        return (
            "version = 1\n\n"
            "[[package]]\n"
            'name = "example"\nversion = "0.1.0"\n\n'
            "[[package]]\n"
            'name = "tei-rapporteur"\n'
            f'version = "{version}"\n'
            f"source = {{ {source} }}\n"
        )

    def test_an_unsupported_tuple_names_the_package_and_boundary(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """The pinned package and the range it supports are both reported."""
        manifest = '[native]\npackage = "tei-rapporteur"\nsupported_python = ">=3.14"\n'

        outcome = run_preflight(
            repository(
                tmp_path / "repo",
                Checkout(
                    manifest=manifest,
                    extra={
                        "uv.lock": lock_document(
                            "0.3.1",
                            'git = "https://example.invalid/tei.git?rev=abc123"',
                        )
                    },
                ),
            ),
            project_python="3.13",
        )

        assert outcome.exit_code == 1
        found = outcome.findings("native")
        assert "tei-rapporteur's supported-python" in found
        assert ">=3.14" in found

    def test_unknown_support_stays_unknown(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """No declared range is reported as unknown, never as compatible."""
        manifest = '[native]\npackage = "tei-rapporteur"\n'

        outcome = run_preflight(
            repository(
                tmp_path / "repo",
                Checkout(
                    manifest=manifest,
                    extra={"uv.lock": lock_document("0.3.1", 'registry = "x"')},
                ),
            ),
            project_python="3.13",
        )

        assert outcome.exit_code == 0, outcome.stdout
        assert outcome.phases["native"]["status"] == "unknown"
        assert "unknown rather than established" in outcome.findings("native")

    def test_a_revision_that_the_lock_does_not_pin_is_refused(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """Dependency identity includes the revision, not just the name."""
        manifest = (
            "[native]\n"
            'package = "tei-rapporteur"\n'
            'revision = "def456"\n'
            'supported_python = ">=3.12"\n'
        )

        outcome = run_preflight(
            repository(
                tmp_path / "repo",
                Checkout(
                    manifest=manifest,
                    extra={
                        "uv.lock": lock_document(
                            "0.3.1",
                            'git = "https://example.invalid/tei.git?rev=abc123"',
                        )
                    },
                ),
            ),
            project_python="3.13",
        )

        assert outcome.exit_code == 1
        assert "not the declared revision 'def456'" in outcome.findings("native")

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

        outcome = run_preflight(
            repository(
                tmp_path / "repo",
                Checkout(
                    manifest=manifest,
                    extra={"uv.lock": lock_document("0.3.1", 'registry = "x"')},
                ),
            ),
            project_python="3.13",
        )

        assert outcome.exit_code == 1
        found = outcome.findings("native")
        assert "the native probe for tei-rapporteur failed" in found
        assert "no module named tei" in found


class TestEnvelope:
    """The run publishes its phases and verdict with no mutants in existence."""

    def test_every_phase_is_emitted_in_order(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """The envelope is complete even when nothing has been mutated."""
        # Both commands name the running interpreter: it exists on every
        # platform, so this test checks the envelope rather than which tools
        # the host happens to have. Literal strings, because a Windows
        # interpreter path holds backslashes a basic string would read as
        # escapes.
        manifest = (
            'sandbox_paths = ["README.md"]\n'
            "[[require_commands]]\n"
            f"name = '{sys.executable}'\n"
            "[native]\n"
            'package = "tei-rapporteur"\n'
            "[[checks]]\n"
            'name = "collect"\n'
            f"command = ['{sys.executable}', '-c', 'print(1)']\n"
        )

        outcome = run_preflight(
            repository(
                tmp_path / "repo",
                Checkout(
                    manifest=manifest,
                    also_copy=("conftest.py", "README.md"),
                    extra={
                        "README.md": "# example\n",
                        "uv.lock": lock_document("0.3.1", 'registry = "x"'),
                    },
                ),
            ),
            project_python="3.13",
        )

        assert outcome.exit_code == 0, outcome.stdout
        order = [
            line.partition("=")[2]
            for line in outcome.stdout.splitlines()
            if line.startswith("mutation_preflight_phase=")
        ]
        names = [
            typ.cast("dict[str, object]", json.loads(item))["name"] for item in order
        ]
        assert names == [
            "manifest",
            "interpreter",
            "native",
            "sandbox",
            "commands",
            "checks",
        ]
        assert outcome.result == "pass"

    def test_the_report_is_written_without_mutation_results(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """The JSON report is the artefact a later stage reads, so it always exists."""
        outcome = run_preflight(repository(tmp_path / "repo"), project_python="3.13")

        report = json.loads(
            (tmp_path / "out" / "mutation-preflight.json").read_text(encoding="utf-8")
        )
        assert report["result"] == "pass"
        assert [phase["name"] for phase in report["phases"]] == [
            "manifest",
            "interpreter",
        ]
        assert outcome.phases["manifest"]["status"] == "skipped"

    def test_the_step_summary_is_appended_when_the_job_asks(
        self,
        tmp_path: Path,
        run_preflight: typ.Callable[..., Outcome],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The Markdown summary rides on GITHUB_STEP_SUMMARY like the other gates."""
        summary = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

        outcome = run_preflight(repository(tmp_path / "repo"), project_python="3.13")

        assert outcome.exit_code == 0, outcome.stdout
        rendered = summary.read_text(encoding="utf-8")
        assert "## Mutation environment preflight" in rendered
        assert "| interpreter | pass |" in rendered

    def test_an_unknown_lane_is_refused(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A lane this helper cannot check fails instead of borrowing another's."""
        outcome = run_preflight(
            repository(tmp_path / "repo"), lane="stryker", project_python="3.13"
        )

        assert outcome.exit_code == 2
        assert outcome.result == "fail"
