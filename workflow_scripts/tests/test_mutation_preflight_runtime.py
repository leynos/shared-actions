"""The mutation preflight's runtime phases: tools, native support, envelope.

Each case is one of the failures issue #591 recorded: an absent mold or
mdtablefix, an unsupported native tuple, the probe that must not run under a
rejected interpreter, and the envelope that must exist before any mutant does.
"""

from __future__ import annotations

import dataclasses as dc
import json
import sys
import typing as typ

import pytest

from workflow_scripts.mutationpreflight.constants import LOG_BOUND_BYTES
from workflow_scripts.mutationpreflight.models import (
    PhaseResult,
    Ran,
    Report,
    Runtimes,
)
from workflow_scripts.mutationpreflight.reporting import render_summary
from workflow_scripts.mutationpreflight.running import (
    TRUNCATION_PREFIX,
    bounded,
    complete_stream,
    last_line,
    reports_version,
)
from workflow_scripts.tests.mutation_preflight_support import (
    POSIX_SHIMS_ONLY,
    RULE_AVAILABLE,
    Checkout,
    Outcome,
    lock_document,
    outcome_for,
)

if typ.TYPE_CHECKING:
    from pathlib import Path


def _commands_outcome(
    run_preflight: typ.Callable[..., Outcome], root: Path, manifest: str
) -> Outcome:
    """Run the preflight for a checkout declaring command requirements."""
    return outcome_for(
        run_preflight,
        root,
        Checkout(manifest=manifest),
        project_python="3.13",
    )


@dc.dataclass(frozen=True)
class CommandCase:
    """One declaration, the tool that answers it, and the verdict it earns."""

    tool: str
    body: str
    manifest: str
    status: str
    finding: str = ""


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
        outcome = _commands_outcome(
            run_preflight,
            tmp_path / "repo",
            '[[require_commands]]\nname = "mdtablefix"\n',
        )

        assert outcome.exit_code == 1
        assert "required command 'mdtablefix'" in outcome.findings("commands")
        assert "PATH" in outcome.findings("commands")

    @POSIX_SHIMS_ONLY
    @pytest.mark.parametrize(
        "case",
        [
            CommandCase(
                tool="mold",
                body='echo "mold 2.41.0 (compatible with GNU ld)"',
                manifest='[[require_commands]]\nname = "mold"\nversion = "2.41"\n',
                status="pass",
            ),
            CommandCase(
                tool="probe",
                body="exit 3",
                manifest='[[require_commands]]\nname = "probe"\n',
                status="pass",
            ),
            CommandCase(
                tool="mold",
                body='echo "mold 2.41.0"',
                manifest='[[require_commands]]\nname = "mold"\nversion = "2.4"\n',
                status="fail",
                finding="does not report '2.4'",
            ),
            CommandCase(
                tool="mold",
                body='echo "mold 2.41.0"; exit 3',
                manifest='[[require_commands]]\nname = "mold"\nversion = "2.41"\n',
                status="fail",
                finding="exited 3",
            ),
        ],
        ids=["pinned-pass", "unpinned-resolves", "nearby-version", "failed-probe"],
    )
    def test_a_command_is_judged_by_what_it_declares(
        self,
        tmp_path: Path,
        run_preflight: typ.Callable[..., Outcome],
        install_tool: typ.Callable[..., None],
        case: CommandCase,
    ) -> None:
        """A pinned version must match; an unpinned command must only resolve."""
        install_tool(case.tool, case.body)

        outcome = _commands_outcome(run_preflight, tmp_path / "repo", case.manifest)

        assert outcome.phases["commands"]["status"] == case.status, outcome.stdout
        if case.finding:
            assert case.finding in outcome.findings("commands")

    @POSIX_SHIMS_ONLY
    def test_repeated_command_names_keep_their_own_evidence(
        self,
        tmp_path: Path,
        run_preflight: typ.Callable[..., Outcome],
        install_tool: typ.Callable[..., None],
    ) -> None:
        """Two entries for one tool must not leave only the later probe's result."""
        install_tool("probe", 'echo "probe 2.4.1"')
        manifest = (
            "[[require_commands]]\n"
            'name = "probe"\nversion = "2.4.1"\n'
            "[[require_commands]]\n"
            'name = "probe"\nversion = "2.4"\n'
            'version_args = ["--report"]\n'
        )

        outcome = _commands_outcome(run_preflight, tmp_path / "repo", manifest)

        assert outcome.phases["commands"]["status"] == "pass", outcome.stdout
        results = typ.cast(
            "list[dict[str, object]]", outcome.detail("commands")["results"]
        )
        assert [(item["index"], item["name"]) for item in results] == [
            (0, "probe"),
            (1, "probe"),
        ], "each declaration entry must keep its own indexed evidence"

    @POSIX_SHIMS_ONLY
    def test_a_version_probe_selects_the_validated_project_interpreter(
        self,
        tmp_path: Path,
        run_preflight: typ.Callable[..., Outcome],
        install_tool: typ.Callable[..., None],
    ) -> None:
        """A probe that runs the project must not inherit another interpreter."""
        # The shim fails unless the run passed the project interpreter to it,
        # so the phase's verdict is the assertion rather than a record of it.
        install_tool(
            "probe",
            'echo "probe 2.4.1"\ntest "$UV_PYTHON" = "3.13" || exit 9',
        )
        manifest = '[[require_commands]]\nname = "probe"\nversion = "2.4.1"\n'

        outcome = _commands_outcome(run_preflight, tmp_path / "repo", manifest)

        assert outcome.exit_code == 0, outcome.stdout
        assert outcome.phases["commands"]["status"] == "pass", (
            f"the probe must see UV_PYTHON=3.13: {outcome.findings('commands')}"
        )


class TestVersionMatching:
    """A reported version is compared as a token, not searched for as text."""

    @pytest.mark.parametrize(
        ("printed", "wanted", "accepted"),
        [
            ("mold 2.41.0", "2.4", False),
            ("mold 2.4.1", "2.4", True),
            ("mold 2.4", "2.4", True),
            ("mold 1.2.3rc1", "1.2.3", False),
            ("mold 1.2.3.post1", "1.2.3", False),
            ("mold 1.2.3beta1", "1.2.3", False),
            ("mold 1.2.3-rc1", "1.2.3", False),
            ("mold 1.2.3_rc1", "1.2.3", False),
            ("mold 1.2.3-beta", "1.2.3", False),
            ("mold 1.2.3", "1.2.3", True),
            ("mold 0.9.0rc1", "0.9.0rc1", True),
            ("mold 0.9.0rc1", "0.9.0", False),
            ("mold 1.2.3build", "1.2.3", True),
            ("mold 1.2.30", "1.2.3", False),
            ("mold 1.2.3rc1", "1.2.3rc1", True),
            ("mold 1.2.3rc1.dev1", "1.2.3rc1", False),
            ("mold 1.2.3rc1.dev1", "1.2.3", False),
            ("mold 1.2.3.rc1", "1.2.3", False),
        ],
    )
    def test_a_token_must_equal_the_declared_version_or_extend_it(
        self, printed: str, wanted: str, *, accepted: bool
    ) -> None:
        """A pre- or post-release suffix stays part of the token it qualifies."""
        assert reports_version(printed, wanted) is accepted


class TestBoundedOutput:
    """A truncated stream still names a line of its retained output."""

    def test_the_truncation_marker_is_not_the_diagnostic_line(self) -> None:
        """The marker counts dropped bytes; it does not say what failed."""
        text = "bad line\n" + "x" * (LOG_BOUND_BYTES + 1)
        outcome = Ran(exit_code=1, stdout="", stderr=bounded(text))

        assert "bytes truncated" in outcome.stderr
        assert "bytes truncated" not in last_line(outcome)
        assert last_line(outcome)

    def test_a_stream_the_bound_holds_exactly_is_not_truncated(self) -> None:
        """The bound is a ceiling, so a stream exactly at it is complete."""
        text = "x" * LOG_BOUND_BYTES

        assert bounded(text) == text, (
            "a stream the bound holds exactly must be kept whole"
        )
        assert "bytes truncated" not in bounded(text)

    def test_one_byte_past_the_bound_is_truncated(self) -> None:
        """A byte over the ceiling is counted and marked, not lost silently."""
        text = "x" * (LOG_BOUND_BYTES + 1)

        kept = bounded(text)

        assert kept.partition("\n")[0] == "x" * LOG_BOUND_BYTES, (
            "the retained prefix must be the bound's worth of bytes"
        )
        assert "bytes truncated" in kept, (
            "a stream over the bound must say how much was dropped"
        )

    def test_a_token_cut_at_the_bound_is_not_matched(self) -> None:
        """A version that ends at the bound is unproven, so it is not a match."""
        marker = f"{TRUNCATION_PREFIX}900 bytes truncated]"

        cut = complete_stream(f"probe 2.4{marker}")
        earlier = complete_stream(f"probe 2.41.0\n2.4{marker}")

        assert not reports_version(cut, "2.4"), (
            "the token the truncation may have extended must not satisfy a pin"
        )
        assert reports_version(earlier, "2.41.0"), (
            "a complete token printed before the flood still counts"
        )


class TestNative:
    """A locked native dependency is named with its support boundary."""

    @staticmethod
    def _checkout(manifest: str, source: str = 'registry = "x"') -> Checkout:
        """Return a checkout declaring *manifest*, with the package locked."""
        return Checkout(
            manifest=manifest,
            extra={"uv.lock": lock_document("0.3.1", source)},
        )

    @RULE_AVAILABLE
    def test_an_unsupported_tuple_names_the_package_and_boundary(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """The pinned package and the range it supports are both reported."""
        manifest = '[native]\npackage = "tei-rapporteur"\nsupported_python = ">=3.14"\n'

        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            self._checkout(
                manifest, 'git = "https://example.invalid/tei.git?rev=abc123"'
            ),
            project_python="3.13",
        )

        assert outcome.exit_code == 1
        found = outcome.findings("native")
        assert "tei-rapporteur's supported-python" in found
        assert ">=3.14" in found

    def _unknown_outcome(
        self,
        runner: typ.Callable[..., Outcome],
        root: Path,
        manifest: str,
        **inputs: str,
    ) -> Outcome:
        """Return the run's outcome, having asserted support stayed unknown."""
        outcome = outcome_for(runner, root, self._checkout(manifest), **inputs)
        assert outcome.exit_code == 0, outcome.stdout
        assert outcome.phases["native"]["status"] == "unknown"
        return outcome

    def test_a_declared_name_finds_the_lock_entry_it_canonicalises_to(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """An underscore declaration must find the lock's hyphenated entry."""
        manifest = (
            "[native]\n"
            'package = "tei_rapporteur"\n'
            'revision = "abc123"\n'
            'supported_python = ">=3.12"\n'
        )

        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            self._checkout(
                manifest, 'git = "https://example.invalid/tei.git?rev=abc123"'
            ),
            project_python="3.13",
        )

        assert outcome.exit_code == 0, outcome.stdout
        detail = outcome.detail("native")
        assert detail["locked_version"] == "0.3.1", (
            "the locked record must be read through the canonical name"
        )
        assert "not in uv.lock" not in outcome.findings("native"), (
            "a name the lock canonicalises to must not be reported as absent"
        )

    def test_a_supported_range_with_a_matching_pin_passes(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """Supported support is reported as a pass, not merely as not-unknown."""
        manifest = (
            "[native]\n"
            'package = "tei-rapporteur"\n'
            'revision = "abc123"\n'
            'supported_python = ">=3.12"\n'
        )

        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            self._checkout(
                manifest, 'git = "https://example.invalid/tei.git?rev=abc123"'
            ),
            project_python="3.13",
        )

        assert outcome.exit_code == 0, outcome.stdout
        assert outcome.phases["native"]["status"] == "pass", (
            "a supported range and a matching pin must pass the native phase"
        )

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
            self._checkout(manifest),
            project_python="3.13",
        )

        assert outcome.exit_code == 0, outcome.stdout
        probe = typ.cast("dict[str, object]", outcome.detail("native")["probe"])
        assert probe["exit_code"] == 0, "the probe's exit status must be recorded"

    def test_unknown_support_stays_unknown(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """No declared range is reported as unknown, never as compatible."""
        outcome = self._unknown_outcome(
            run_preflight,
            tmp_path / "repo",
            '[native]\npackage = "tei-rapporteur"\n',
            project_python="3.13",
        )

        assert "unknown rather than established" in outcome.findings("native")

    def test_a_declared_range_is_unknown_without_a_project_interpreter(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A range that was never judged must not read as a range that holds."""
        outcome = self._unknown_outcome(
            run_preflight,
            tmp_path / "repo",
            '[native]\npackage = "tei-rapporteur"\nsupported_python = ">=3.12"\n',
            project_python="",
            helper_python="3.13",
        )

        assert "no project interpreter was supplied" in outcome.findings("native")

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

        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            self._checkout(
                manifest, 'git = "https://example.invalid/tei.git?rev=abc123"'
            ),
            project_python="3.13",
        )

        assert outcome.exit_code == 1
        assert "not the declared revision 'def456'" in outcome.findings("native")

    def test_a_revision_in_the_repository_url_is_not_a_pinned_revision(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """The revision is read from the fields that record it, not searched for."""
        manifest = (
            "[native]\n"
            'package = "tei-rapporteur"\n'
            'revision = "abc123"\n'
            'supported_python = ">=3.12"\n'
        )

        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            self._checkout(manifest, 'git = "https://example.invalid/abc123/tei.git"'),
            project_python="3.13",
        )

        assert outcome.exit_code == 1
        found = outcome.findings("native")
        assert "records no pinned revision" in found
        assert "not the declared revision" not in found

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
            self._checkout(manifest),
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
            self._checkout(manifest),
            project_python="3.13",
        )

        assert outcome.exit_code == 1
        found = outcome.findings("native")
        assert "failed with exit code 1: error: first" in found
        assert "warning: last" not in found


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

        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            Checkout(
                manifest=manifest,
                also_copy=("conftest.py", "README.md"),
                extra={
                    "README.md": "# example\n",
                    "uv.lock": lock_document("0.3.1", 'registry = "x"'),
                },
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
        outcome = outcome_for(
            run_preflight, tmp_path / "repo", Checkout(), project_python="3.13"
        )

        report = json.loads(
            (tmp_path / "out" / "mutation-preflight.json").read_text(encoding="utf-8")
        )
        assert report["result"] == "pass"
        assert [phase["name"] for phase in report["phases"]] == [
            "manifest",
            "interpreter",
            "native",
            "sandbox",
            "commands",
            "checks",
        ], "a phase the declaration would have enabled is reported, not omitted"
        assert outcome.phases["manifest"]["status"] == "skipped"

    def test_an_unwritable_report_fails_the_run(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A verdict nobody can read must not be reported as a pass."""
        destination = tmp_path / "out" / "mutation-preflight.json"
        destination.mkdir(parents=True)

        outcome = outcome_for(
            run_preflight, tmp_path / "repo", Checkout(), project_python="3.13"
        )

        assert outcome.exit_code == 1
        assert outcome.result == "fail"
        assert not destination.is_file()

    def test_the_step_summary_is_appended_when_the_job_asks(
        self,
        tmp_path: Path,
        run_preflight: typ.Callable[..., Outcome],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The Markdown summary rides on GITHUB_STEP_SUMMARY like the other gates."""
        summary = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

        outcome = outcome_for(
            run_preflight, tmp_path / "repo", Checkout(), project_python="3.13"
        )

        assert outcome.exit_code == 0, outcome.stdout
        rendered = summary.read_text(encoding="utf-8")
        assert "## Mutation environment preflight" in rendered
        assert "| interpreter | pass |" in rendered

    def test_a_step_summary_that_cannot_be_written_fails_the_verdict(
        self,
        tmp_path: Path,
        run_preflight: typ.Callable[..., Outcome],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A report whose summary failed must not be published as a pass."""
        summary = tmp_path / "summary.md"
        summary.mkdir()
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

        outcome = outcome_for(
            run_preflight, tmp_path / "repo", Checkout(), project_python="3.13"
        )

        assert outcome.exit_code == 1, outcome.stdout
        assert outcome.result == "fail"
        written = json.loads(
            (tmp_path / "out" / "mutation-preflight.json").read_text(encoding="utf-8")
        )
        assert written["result"] == "fail", (
            "the JSON verdict must agree with the exit status when the summary "
            "could not be appended"
        )
        assert written["reporting"], (
            "the report must record why its summary was not published"
        )

    def test_an_unknown_lane_is_refused(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A lane this helper cannot check fails instead of borrowing another's."""
        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            Checkout(),
            lane="stryker",
            project_python="3.13",
        )

        assert outcome.exit_code == 2
        assert outcome.result == "fail"


class TestSummaryRow:
    """A finding cannot escape the table row it is rendered in."""

    def test_a_multiline_finding_stays_in_one_row(self) -> None:
        """ConfigParser's multi-line message must not end the row early."""
        finding = "setup.cfg could not be read: File contains no section\nheaders"
        report = Report(
            lane="mutmut",
            runtimes=Runtimes(helper="3.14.7", project="3.13"),
            results=(PhaseResult("sandbox", "unknown", (finding,)),),
            verdict="pass",
        )

        rendered = render_summary(report)

        rows = [line for line in rendered.splitlines() if line.startswith("| sandbox")]
        assert rows == [
            (
                "| sandbox | unknown | setup.cfg could not be read: File contains "
                "no section headers |"
            )
        ]

    def test_a_backslash_cannot_neutralize_the_pipe_escape(self) -> None:
        """Escaping the pipe after a backslash would leave the pipe unescaped."""
        report = Report(
            lane="mutmut",
            runtimes=Runtimes(helper="3.14.7", project="3.13"),
            results=(PhaseResult("sandbox", "fail", ("a\\|b",)),),
            verdict="fail",
        )

        rendered = render_summary(report)

        assert "a\\\\\\|b" in rendered
