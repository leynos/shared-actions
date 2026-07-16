"""Unit tests for the mutmut run-and-summarize helper script."""

from __future__ import annotations

import stat
import sys
import typing as typ

import pytest
from plumbum import local

from workflow_scripts import mutation_run_mutmut as run_mutmut

if typ.TYPE_CHECKING:
    from pathlib import Path

# The reusable workflows only run on ubuntu-latest; the fake tool shims
# are POSIX shell scripts, so Windows falls through to the real tools
# (where a real mutmut failure can coincidentally satisfy assertions).
POSIX_SHIMS_ONLY = pytest.mark.skipif(
    sys.platform == "win32", reason="fake uv helper emits POSIX sh"
)

RESULTS_TEXT = """\
warning: The config paths_to_mutate is deprecated.
    mypkg.calc.x_add__mutmut_1: killed
    mypkg.calc.x_clamp__mutmut_1: survived
    mypkg.calc.x_clamp__mutmut_2: no tests
    mypkg.io.x_read__mutmut_3: timeout
"""


class TestFilesToModuleGlobs:
    """Translation of changed files into mutant-name globs."""

    def test_src_layout_files_map_to_module_globs(self) -> None:
        """``src/pkg/mod.py`` becomes ``pkg.mod.*``."""
        globs = run_mutmut.files_to_module_globs(
            "src/mypkg/calc.py src/mypkg/io.py", "src/"
        )
        assert globs == ["mypkg.calc.*", "mypkg.io.*"], (
            "src-layout files should translate to module globs"
        )

    def test_init_maps_to_package_and_duplicates_collapse(self) -> None:
        """``__init__.py`` maps to its package; duplicates are dropped."""
        globs = run_mutmut.files_to_module_globs(
            "src/mypkg/__init__.py src/mypkg/__init__.py", "src/"
        )
        assert globs == ["mypkg.*"], (
            "__init__.py should map to its package glob with duplicates collapsed"
        )

    def test_non_python_and_bare_prefix_are_ignored(self) -> None:
        """Non-Python paths and empty translations produce nothing."""
        assert run_mutmut.files_to_module_globs("README.md src/", "src/") == [], (
            "non-Python paths and bare prefixes should produce no globs"
        )

    def test_file_outside_prefix_keeps_full_module_path(self) -> None:
        """A file outside the strip prefix is translated without stripping."""
        assert run_mutmut.files_to_module_globs("other/mod.py", "src/") == [
            "other.mod.*"
        ], "files outside the prefix should keep their full module path"

    def test_prefix_strip_removes_only_trailing_separator(self) -> None:
        """Only the trailing separator is stripped; leading ones are kept."""
        assert run_mutmut.files_to_module_globs("src/mod.py", "/src/") == [
            "src.mod.*"
        ], "a leading slash in the prefix must not strip the file's own prefix"


class TestParseResults:
    """Parsing of ``mutmut results --all true`` output."""

    def test_result_lines_parse_and_noise_is_ignored(self) -> None:
        """Result lines parse; warnings and blanks are skipped."""
        results = run_mutmut.parse_results(RESULTS_TEXT)
        assert [result.status for result in results] == [
            "killed",
            "survived",
            "no tests",
            "timeout",
        ], "each result line should parse to its status with noise skipped"
        assert results[1].name == "mypkg.calc.x_clamp__mutmut_1", (
            "parsed results should retain the mutant name"
        )

    def test_names_with_spaces_are_treated_as_noise(self) -> None:
        """A ``name: status`` line whose name has a space is not a result."""
        assert run_mutmut.parse_results("foo bar__mutmut_2: survived") == [], (
            "a spaced name should be rejected as noise, not parsed as a mutant"
        )

    def test_counts_group_by_status(self) -> None:
        """Status counts aggregate across mutants."""
        results = run_mutmut.parse_results(RESULTS_TEXT)
        assert run_mutmut.count_statuses(results) == {
            "killed": 1,
            "survived": 1,
            "no tests": 1,
            "timeout": 1,
        }, "status counts should aggregate across mutants"

    def test_counts_accumulate_repeated_status(self) -> None:
        """A status seen twice is counted twice, not reset to one."""
        results = [
            run_mutmut.MutantResult(name="a__mutmut_1", status="killed"),
            run_mutmut.MutantResult(name="b__mutmut_1", status="killed"),
        ]
        assert run_mutmut.count_statuses(results) == {"killed": 2}, (
            "repeated statuses should accumulate rather than reset"
        )


class TestRenderSummary:
    """Markdown rendering of mutmut results."""

    def test_survivor_table_lists_survived_and_untested(self) -> None:
        """Survived and no-tests mutants appear in the table."""
        rendered = run_mutmut.render_summary(run_mutmut.parse_results(RESULTS_TEXT))
        assert "## Mutation testing results (mutmut)" in rendered, (
            "the summary should carry the mutmut results heading"
        )
        assert "- **killed:** 1" in rendered, "the summary should list the killed count"
        assert "| `mypkg.calc.x_clamp__mutmut_1` | survived |" in rendered, (
            "survived mutants should appear in the survivor table"
        )
        assert "| `mypkg.calc.x_clamp__mutmut_2` | no tests |" in rendered, (
            "no-tests mutants should appear in the survivor table"
        )
        assert "x_read__mutmut_3" not in rendered.split("Surviving mutants")[1], (
            "timed-out mutants should not appear in the survivor table"
        )

    def test_exact_markdown_layout(self) -> None:
        """The rendered Markdown matches the contracted layout byte-for-byte."""
        results = [
            run_mutmut.MutantResult(name="pkg.mod.x_f__mutmut_1", status="killed"),
            run_mutmut.MutantResult(name="pkg.mod.x_f__mutmut_2", status="killed"),
            run_mutmut.MutantResult(name="pkg.mod.x_g__mutmut_1", status="survived"),
            run_mutmut.MutantResult(name="pkg.mod.x_h__mutmut_1", status="no tests"),
        ]
        expected = (
            "## Mutation testing results (mutmut)\n"
            "\n"
            "- **killed:** 2\n"
            "- **no tests:** 1\n"
            "- **survived:** 1\n"
            "\n"
            "### Surviving mutants\n"
            "\n"
            "Inspect a survivor with `uv run mutmut show <name>`.\n"
            "\n"
            "| Mutant | Status |\n"
            "| ------ | ------ |\n"
            "| `pkg.mod.x_g__mutmut_1` | survived |\n"
            "| `pkg.mod.x_h__mutmut_1` | no tests |\n"
        )
        assert run_mutmut.render_summary(results) == expected, (
            "the summary Markdown should match the contracted layout exactly"
        )

    def test_empty_results_render_message(self) -> None:
        """No mutants yields the exact explanatory message."""
        assert (
            run_mutmut.render_summary([])
            == "## Mutation testing results (mutmut)\n\nNo mutants were tested.\n"
        ), "empty results should render the exact explanatory message"


class TestMutmutCommand:
    """Construction of the ``uv run --with`` argument prefix."""

    def test_pins_version_and_subcommands(self) -> None:
        """The prefix injects the pinned mutmut and the run subcommand."""
        assert run_mutmut._mutmut_command("3.6.0") == [
            "run",
            "--with",
            "mutmut==3.6.0",
            "mutmut",
        ], "the uv prefix should pin the requested mutmut version verbatim"


@pytest.fixture
def fake_uv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Install a fake ``uv`` on PATH that scripts run/results calls.

    ``mutmut run`` invocations exit with ``FAKE_MUTMUT_RUN_EXIT``;
    ``mutmut results`` invocations print canned results. All arguments
    are appended to ``uv-args.txt``.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    args_file = tmp_path / "uv-args.txt"
    results_text = RESULTS_TEXT.replace("\n", "\\n")
    script = bin_dir / "uv"
    script.write_text(
        "#!/bin/sh\n"
        f'printf \'%s \' "$@" >> "{args_file}"\n'
        f"printf '\\n' >> \"{args_file}\"\n"
        'case "$*" in\n'
        f"  *results*) printf '{results_text}' ;;\n"
        '  *run*mutmut*) exit "${FAKE_MUTMUT_RUN_EXIT:-0}" ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}:{local.env['PATH']}")
    monkeypatch.setitem(local.env, "PATH", f"{bin_dir}:{local.env['PATH']}")
    return args_file


class TestMainEntry:
    """End-to-end behaviour of the CLI entry point."""

    def _prepare(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> tuple[Path, Path]:
        """Point summary and results outputs at temp files."""
        summary_file = tmp_path / "summary.md"
        summary_file.touch()
        results_file = tmp_path / "results.txt"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))
        monkeypatch.setenv("INPUT_RESULTS_FILE", str(results_file))
        return summary_file, results_file

    @POSIX_SHIMS_ONLY
    def test_scoped_run_passes_module_globs(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        fake_uv: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Changed files reach mutmut as module globs; summary is written."""
        summary_file, results_file = self._prepare(tmp_path, monkeypatch)
        monkeypatch.setenv("INPUT_FILES", "src/mypkg/calc.py")
        run_mutmut.app([])
        recorded = fake_uv.read_text(encoding="utf-8")
        assert "mutmut run mypkg.calc.*" in recorded.replace("  ", " "), (
            "changed files should reach mutmut run as module globs"
        )
        # Both the run and results sub-invocations must pin the version, so a
        # dropped version anywhere leaves a bare ``mutmut==None`` behind.
        assert "mutmut==None" not in recorded, (
            "every mutmut sub-invocation should pin the requested version"
        )
        assert recorded.count("mutmut==3.6.0") >= 2, (
            "both the run and results invocations should pin the version"
        )
        for token in ("results", "--all", "true"):
            assert token in recorded.split(), (
                f"the results invocation should pass the {token!r} argument"
            )
        assert "survived" in results_file.read_text(encoding="utf-8"), (
            "the results file should capture the mutmut results output"
        )
        assert "Surviving mutants" in summary_file.read_text(encoding="utf-8"), (
            "the job summary should include the survivors section"
        )
        # Structured diagnostics carry the contracted keys and values.
        out = capsys.readouterr().out
        command_line = next(
            line for line in out.splitlines() if "mutation_mutmut_command=" in line
        )
        assert command_line.startswith('mutation_mutmut_command=["uv"'), (
            "the command diagnostic should name uv under its exact key"
        )
        assert "mutmut==3.6.0" in command_line, (
            "the command diagnostic should record the pinned version"
        )
        assert "mutation_mutmut_exit_code=0" in out, (
            "a clean run should report exit code 0 under its exact key"
        )
        counts_line = next(
            line for line in out.splitlines() if "mutation_mutmut_counts=" in line
        )
        assert "survived" in counts_line, (
            "the counts diagnostic should carry the parsed status tallies"
        )

    @POSIX_SHIMS_ONLY
    def test_failing_baseline_propagates_exit_code(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        fake_uv: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A non-zero mutmut run fails the step and logs to stderr."""
        self._prepare(tmp_path, monkeypatch)
        monkeypatch.setenv("FAKE_MUTMUT_RUN_EXIT", "3")
        monkeypatch.setitem(local.env, "FAKE_MUTMUT_RUN_EXIT", "3")
        with pytest.raises(SystemExit) as excinfo:
            run_mutmut.app([])
        assert excinfo.value.code == 3, (
            "a failing mutmut run should propagate its exit code"
        )
        captured = capsys.readouterr()
        assert "mutation_mutmut_error=mutmut run failed with exit code 3" in (
            captured.err
        ), "the failure diagnostic and message should be written to stderr"
        assert "mutation_mutmut_error=" not in captured.out, (
            "the failure diagnostic must not leak onto stdout"
        )

    def test_scope_without_python_files_short_circuits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_uv: Path
    ) -> None:
        """A scope containing no Python files skips mutmut entirely."""
        _, results_file = self._prepare(tmp_path, monkeypatch)
        monkeypatch.setenv("INPUT_FILES", "README.md")
        run_mutmut.app([])
        assert not fake_uv.exists(), (
            "uv should never be invoked when the scope has no Python files"
        )
        assert results_file.read_text(encoding="utf-8") == "", (
            "the results file should stay empty on the short-circuit path"
        )

    def test_missing_summary_env_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A missing GITHUB_STEP_SUMMARY is a hard error."""
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
        with pytest.raises(SystemExit) as excinfo:
            run_mutmut.app([])
        assert excinfo.value.code == 1, (
            "a missing GITHUB_STEP_SUMMARY should exit with code 1"
        )
