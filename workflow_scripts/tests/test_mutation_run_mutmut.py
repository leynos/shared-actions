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

    @pytest.mark.parametrize(
        "path",
        [
            "hooks/test_post_turn_quality_stop_hook.py",
            "src/mypkg/calc_test.py",
            "src/mypkg/conftest.py",
            "src/mypkg/tests/helpers.py",
        ],
    )
    def test_test_files_are_excluded(self, path: str) -> None:
        """Pytest test modules map to no glob: mutmut never mutates them."""
        assert run_mutmut.files_to_module_globs(path, "src/") == [], (
            "test modules are not mutable source and must not become globs"
        )

    def test_test_only_scope_drops_out_leaving_source(self) -> None:
        """A source file survives while its sibling test file is dropped."""
        globs = run_mutmut.files_to_module_globs(
            "src/mypkg/calc.py src/mypkg/test_calc.py", "src/"
        )
        assert globs == ["mypkg.calc.*"], (
            "only the mutable source file should reach mutmut as a glob"
        )


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

    def test_counts_group_by_status(self) -> None:
        """Status counts aggregate across mutants."""
        results = run_mutmut.parse_results(RESULTS_TEXT)
        assert run_mutmut.count_statuses(results) == {
            "killed": 1,
            "survived": 1,
            "no tests": 1,
            "timeout": 1,
        }, "status counts should aggregate across mutants"


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

    def test_empty_results_render_message(self) -> None:
        """No mutants yields an explanatory message."""
        assert "No mutants were tested." in run_mutmut.render_summary([]), (
            "empty results should render an explanatory message"
        )


@pytest.fixture
def fake_uv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Install a fake ``uv`` on PATH that scripts run/results calls.

    ``mutmut run`` invocations exit with ``FAKE_MUTMUT_RUN_EXIT`` and dump
    their environment to ``uv-env.txt``; ``mutmut results`` invocations
    print canned results. All arguments are appended to ``uv-args.txt``.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    args_file = tmp_path / "uv-args.txt"
    env_file = tmp_path / "uv-env.txt"
    results_text = RESULTS_TEXT.replace("\n", "\\n")
    script = bin_dir / "uv"
    script.write_text(
        "#!/bin/sh\n"
        f'printf \'%s \' "$@" >> "{args_file}"\n'
        f"printf '\\n' >> \"{args_file}\"\n"
        'case "$*" in\n'
        f"  *results*) printf '{results_text}' ;;\n"
        f'  *run*mutmut*) env > "{env_file}"; exit "${{FAKE_MUTMUT_RUN_EXIT:-0}}" ;;\n'
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
        """Point summary and results outputs at temp files.

        Ambient ``INPUT_*`` variables are cleared first so a value the
        caller sets in the real CI environment (for example
        ``INPUT_MODULE_PREFIX_STRIP=""`` leaking into mutmut's in-process
        pytest baseline) cannot override the documented script defaults
        that each test means to exercise.
        """
        for name in ("INPUT_FILES", "INPUT_MUTMUT_VERSION", "INPUT_EXTRA_ARGS"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv("INPUT_MODULE_PREFIX_STRIP", raising=False)
        summary_file = tmp_path / "summary.md"
        summary_file.touch()
        results_file = tmp_path / "results.txt"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))
        monkeypatch.setenv("INPUT_RESULTS_FILE", str(results_file))
        return summary_file, results_file

    @POSIX_SHIMS_ONLY
    def test_scoped_run_passes_module_globs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_uv: Path
    ) -> None:
        """Changed files reach mutmut as module globs; summary is written."""
        summary_file, results_file = self._prepare(tmp_path, monkeypatch)
        monkeypatch.setenv("INPUT_FILES", "src/mypkg/calc.py")
        run_mutmut.app([])
        recorded = fake_uv.read_text(encoding="utf-8")
        assert "mutmut run mypkg.calc.*" in recorded.replace("  ", " "), (
            "changed files should reach mutmut run as module globs"
        )
        assert "survived" in results_file.read_text(encoding="utf-8"), (
            "the results file should capture the mutmut results output"
        )
        assert "Surviving mutants" in summary_file.read_text(encoding="utf-8"), (
            "the job summary should include the survivors section"
        )

    @POSIX_SHIMS_ONLY
    def test_failing_baseline_propagates_exit_code(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_uv: Path
    ) -> None:
        """A non-zero mutmut run fails the step with mutmut's own code."""
        self._prepare(tmp_path, monkeypatch)
        monkeypatch.setenv("FAKE_MUTMUT_RUN_EXIT", "3")
        monkeypatch.setitem(local.env, "FAKE_MUTMUT_RUN_EXIT", "3")
        with pytest.raises(SystemExit) as excinfo:
            run_mutmut.app([])
        assert excinfo.value.code == 3, (
            "a failing mutmut run should propagate its exit code"
        )

    @POSIX_SHIMS_ONLY
    def test_workflow_input_env_does_not_leak_into_mutmut(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_uv: Path
    ) -> None:
        """``INPUT_*`` vars are stripped before mutmut runs the baseline.

        Reproduces shared-actions#369: the caller sets
        ``INPUT_MODULE_PREFIX_STRIP=""``; without sanitisation that value
        reaches mutmut's in-process pytest baseline and breaks tests that
        read the ambient environment.
        """
        self._prepare(tmp_path, monkeypatch)
        monkeypatch.setenv("INPUT_FILES", "src/mypkg/calc.py")
        monkeypatch.setitem(local.env, "INPUT_FILES", "src/mypkg/calc.py")
        monkeypatch.setenv("INPUT_MODULE_PREFIX_STRIP", "")
        monkeypatch.setitem(local.env, "INPUT_MODULE_PREFIX_STRIP", "")
        run_mutmut.app([])
        env_dump = (tmp_path / "uv-env.txt").read_text(encoding="utf-8")
        leaked = [line for line in env_dump.splitlines() if line.startswith("INPUT_")]
        assert not leaked, (
            f"no INPUT_* variable should reach the mutmut subprocess, found {leaked}"
        )

    def test_test_only_scope_short_circuits_with_summary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_uv: Path
    ) -> None:
        """A test-only change window skips mutmut and notes the no-op.

        Reproduces agent-helper-scripts#74: a lone changed test file maps
        to no mutants, so the run short-circuits as a graceful no-op
        instead of aborting mutmut with an empty filter.
        """
        summary_file, results_file = self._prepare(tmp_path, monkeypatch)
        monkeypatch.setenv("INPUT_FILES", "hooks/test_post_turn_quality_stop_hook.py")
        run_mutmut.app([])
        assert not fake_uv.exists(), (
            "uv should never be invoked when the scope has no mutable source"
        )
        assert results_file.read_text(encoding="utf-8") == "", (
            "the results file should stay empty on the short-circuit path"
        )
        assert "no mutable source" in summary_file.read_text(encoding="utf-8"), (
            "the job summary should record the graceful no-op"
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
