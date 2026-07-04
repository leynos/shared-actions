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
        assert globs == ["mypkg.calc.*", "mypkg.io.*"]

    def test_init_maps_to_package_and_duplicates_collapse(self) -> None:
        """``__init__.py`` maps to its package; duplicates are dropped."""
        globs = run_mutmut.files_to_module_globs(
            "src/mypkg/__init__.py src/mypkg/__init__.py", "src/"
        )
        assert globs == ["mypkg.*"]

    def test_non_python_and_bare_prefix_are_ignored(self) -> None:
        """Non-Python paths and empty translations produce nothing."""
        assert run_mutmut.files_to_module_globs("README.md src/", "src/") == []


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
        ]
        assert results[1].name == "mypkg.calc.x_clamp__mutmut_1"

    def test_counts_group_by_status(self) -> None:
        """Status counts aggregate across mutants."""
        results = run_mutmut.parse_results(RESULTS_TEXT)
        assert run_mutmut.count_statuses(results) == {
            "killed": 1,
            "survived": 1,
            "no tests": 1,
            "timeout": 1,
        }


class TestRenderSummary:
    """Markdown rendering of mutmut results."""

    def test_survivor_table_lists_survived_and_untested(self) -> None:
        """Survived and no-tests mutants appear in the table."""
        rendered = run_mutmut.render_summary(run_mutmut.parse_results(RESULTS_TEXT))
        assert "## Mutation testing results (mutmut)" in rendered
        assert "- **killed:** 1" in rendered
        assert "| `mypkg.calc.x_clamp__mutmut_1` | survived |" in rendered
        assert "| `mypkg.calc.x_clamp__mutmut_2` | no tests |" in rendered
        assert "x_read__mutmut_3" not in rendered.split("Surviving mutants")[1]

    def test_empty_results_render_message(self) -> None:
        """No mutants yields an explanatory message."""
        assert "No mutants were tested." in run_mutmut.render_summary([])


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
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_uv: Path
    ) -> None:
        """Changed files reach mutmut as module globs; summary is written."""
        summary_file, results_file = self._prepare(tmp_path, monkeypatch)
        monkeypatch.setenv("INPUT_FILES", "src/mypkg/calc.py")
        run_mutmut.app([])
        recorded = fake_uv.read_text(encoding="utf-8")
        assert "mutmut run mypkg.calc.*" in recorded.replace("  ", " ")
        assert "survived" in results_file.read_text(encoding="utf-8")
        assert "Surviving mutants" in summary_file.read_text(encoding="utf-8")

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
        assert excinfo.value.code == 3

    def test_scope_without_python_files_short_circuits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_uv: Path
    ) -> None:
        """A scope containing no Python files skips mutmut entirely."""
        _, results_file = self._prepare(tmp_path, monkeypatch)
        monkeypatch.setenv("INPUT_FILES", "README.md")
        run_mutmut.app([])
        assert not fake_uv.exists()
        assert results_file.read_text(encoding="utf-8") == ""

    def test_missing_summary_env_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A missing GITHUB_STEP_SUMMARY is a hard error."""
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
        with pytest.raises(SystemExit) as excinfo:
            run_mutmut.app([])
        assert excinfo.value.code == 1
