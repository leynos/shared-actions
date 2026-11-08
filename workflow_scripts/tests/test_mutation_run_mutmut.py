"""Unit tests for the mutmut run-and-summarize helper script."""

from __future__ import annotations

import configparser
import os
import stat
import sys
import tomllib
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
            "src/mypkg/calc.py src/mypkg/io.py",
            "src/",
            run_mutmut.MutmutScope(source_paths=("src/",)),
        )
        assert globs == ["mypkg.calc.*", "mypkg.io.*"], (
            "src-layout files should translate to module globs"
        )

    def test_init_maps_to_package_and_duplicates_collapse(self) -> None:
        """``__init__.py`` maps to its package; duplicates are dropped."""
        globs = run_mutmut.files_to_module_globs(
            "src/mypkg/__init__.py src/mypkg/__init__.py",
            "src/",
            run_mutmut.MutmutScope(source_paths=("src/",)),
        )
        assert globs == ["mypkg.*"], (
            "__init__.py should map to its package glob with duplicates collapsed"
        )

    def test_non_python_and_bare_prefix_are_ignored(self) -> None:
        """Non-Python paths and empty translations produce nothing."""
        scope = run_mutmut.MutmutScope(source_paths=("src/",))
        assert run_mutmut.files_to_module_globs("README.md src/", "src/", scope) == (
            []
        ), "non-Python paths and bare prefixes should produce no globs"

    @pytest.mark.parametrize(
        ("files", "scope", "expected_globs", "reason"),
        [
            pytest.param(
                "tests/test_calc.py",
                run_mutmut.MutmutScope(source_paths=("src/",)),
                [],
                "files outside source_paths must not reach mutmut",
                id="outside-source-paths",
            ),
            pytest.param(
                "src/mypkg/tests/helpers.py",
                run_mutmut.MutmutScope(
                    source_paths=("src/",), do_not_mutate=("src/**/tests/*",)
                ),
                [],
                "do_not_mutate paths must not reach mutmut",
                id="do-not-mutate",
            ),
            pytest.param(
                "src/pkg/api/client.py src/pkg/models.py",
                run_mutmut.MutmutScope(
                    source_paths=("src/",), only_mutate=("src/pkg/api/*",)
                ),
                ["pkg.api.client.*"],
                "only_mutate must narrow source_paths",
                id="only-mutate",
            ),
            pytest.param(
                "src/mypkg/calc.py src/mypkg/test_calc.py",
                run_mutmut.MutmutScope(
                    source_paths=("src/",), do_not_mutate=("src/**/test_*.py",)
                ),
                ["mypkg.calc.*"],
                "only mutable source files should reach mutmut",
                id="test-only-exclusion",
            ),
        ],
    )
    def test_scope_patterns_filter_changed_files(
        self,
        files: str,
        scope: run_mutmut.MutmutScope,
        expected_globs: list[str],
        reason: str,
    ) -> None:
        """Configured source and exclusion patterns filter changed files."""
        globs = run_mutmut.files_to_module_globs(files, "src/", scope)
        assert globs == expected_globs, reason

    def test_glob_patterns_do_not_match_by_basename(self) -> None:
        """Glob patterns match paths, not matching basenames anywhere."""
        scope = run_mutmut.MutmutScope(source_paths=("src/",), only_mutate=("calc.py",))
        globs = run_mutmut.files_to_module_globs("src/pkg/calc.py", "src/", scope)
        assert globs == [], "only_mutate patterns must match the full relative path"

    def test_configured_source_paths_preserve_test_like_module_names(self) -> None:
        """Mutmut source configuration overrides filename and directory hints."""
        scope = run_mutmut.MutmutScope(source_paths=("src/",))
        globs = run_mutmut.files_to_module_globs(
            "src/pkg/request_test.py src/pkg/tests/helpers.py", "src/", scope
        )
        assert globs == ["pkg.request_test.*", "pkg.tests.helpers.*"], (
            "modules included by source_paths must remain mutable regardless of name"
        )


class TestMutmutScopeConfig:
    """Loading of mutmut's file-selection configuration."""

    def test_loads_path_filters_from_pyproject(self, tmp_path: Path) -> None:
        """The tool.mutmut table supplies source and exclusion patterns."""
        (tmp_path / "pyproject.toml").write_text(
            "[tool.mutmut]\n"
            'source_paths = ["src/"]\n'
            'only_mutate = ["src/api/*"]\n'
            'do_not_mutate = ["src/api/tests/*"]\n',
            encoding="utf-8",
        )
        scope = run_mutmut._load_mutmut_scope(tmp_path)
        assert scope == run_mutmut.MutmutScope(
            source_paths=("src/",),
            only_mutate=("src/api/*",),
            do_not_mutate=("src/api/tests/*",),
        )

    def test_loads_path_filters_from_setup_cfg(self, tmp_path: Path) -> None:
        """The mutmut section supports setup.cfg path-list values."""
        (tmp_path / "setup.cfg").write_text(
            "[mutmut]\nsource_paths =\n    src/\ndo_not_mutate =\n    src/tests/*\n",
            encoding="utf-8",
        )
        scope = run_mutmut._load_mutmut_scope(tmp_path)
        assert scope == run_mutmut.MutmutScope(
            source_paths=("src/",), do_not_mutate=("src/tests/*",)
        )

    @pytest.mark.parametrize(
        ("contents", "expected_type", "expected_message"),
        [
            pytest.param(
                None,
                ValueError,
                "mutmut configuration must define source_paths",
                id="missing-configuration",
            ),
            pytest.param(
                "[tool.mutmut]\n",
                ValueError,
                "mutmut source_paths must contain at least one path",
                id="missing-source-paths",
            ),
            pytest.param(
                "[tool.mutmut]\nsource_paths = []\n",
                ValueError,
                "mutmut source_paths must contain at least one path",
                id="empty-source-paths",
            ),
            pytest.param(
                "tool = 1\n",
                TypeError,
                "pyproject.toml [tool] must be a table",
                id="non-table-tool-setting",
            ),
            pytest.param(
                "[tool]\nmutmut = 1\n",
                TypeError,
                "pyproject.toml [tool.mutmut] must be a table",
                id="non-table-mutmut-setting",
            ),
            pytest.param(
                '[tool.mutmut]\nsource_paths = ["src/", 1]\n',
                TypeError,
                "mutmut source_paths must contain only strings",
                id="non-string-source-path",
            ),
        ],
    )
    def test_rejects_invalid_pyproject_scope_configuration(
        self,
        tmp_path: Path,
        contents: str | None,
        expected_type: type[Exception],
        expected_message: str,
    ) -> None:
        """Invalid or missing pyproject scope settings fail with context."""
        if contents is not None:
            (tmp_path / "pyproject.toml").write_text(contents, encoding="utf-8")

        with pytest.raises(expected_type) as excinfo:
            run_mutmut._load_mutmut_scope(tmp_path)

        assert expected_message in str(excinfo.value), (
            "scope configuration errors should name the invalid setting"
        )

    def test_rejects_malformed_pyproject_toml(self, tmp_path: Path) -> None:
        """Malformed pyproject TOML fails instead of yielding an empty scope."""
        (tmp_path / "pyproject.toml").write_text(
            '[tool.mutmut\nsource_paths = ["src/"]\n', encoding="utf-8"
        )

        with pytest.raises(tomllib.TOMLDecodeError):
            run_mutmut._load_mutmut_scope(tmp_path)

    @pytest.mark.parametrize(
        "contents",
        [
            pytest.param("[mutmut]\n", id="missing-source-paths"),
            pytest.param("[mutmut]\nsource_paths =\n", id="empty-source-paths"),
        ],
    )
    def test_rejects_missing_or_empty_setup_cfg_source_paths(
        self, tmp_path: Path, contents: str
    ) -> None:
        """setup.cfg must also define at least one source path."""
        (tmp_path / "setup.cfg").write_text(contents, encoding="utf-8")

        with pytest.raises(ValueError, match="source_paths"):
            run_mutmut._load_mutmut_scope(tmp_path)

    def test_rejects_malformed_setup_cfg(self, tmp_path: Path) -> None:
        """Malformed setup.cfg syntax fails instead of yielding an empty scope."""
        (tmp_path / "setup.cfg").write_text(
            "[mutmut\nsource_paths = src/\n", encoding="utf-8"
        )

        with pytest.raises(configparser.Error):
            run_mutmut._load_mutmut_scope(tmp_path)


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
        """Prepare a clean temporary caller project and summary outputs."""
        # Ignore workflow inputs inherited from the surrounding CI process.
        for name in ("INPUT_FILES", "INPUT_MUTMUT_VERSION", "INPUT_EXTRA_ARGS"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv("INPUT_MODULE_PREFIX_STRIP", raising=False)
        (tmp_path / "pyproject.toml").write_text(
            "[tool.mutmut]\n"
            'source_paths = ["src/"]\n'
            'do_not_mutate = ["src/**/test_*.py", "src/**/tests/*"]\n',
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
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
    def test_full_run_does_not_load_changed_file_scope(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_uv: Path
    ) -> None:
        """An empty file list runs mutmut without requiring helper scope config."""
        self._prepare(tmp_path, monkeypatch)
        (tmp_path / "pyproject.toml").unlink()

        run_mutmut.app([])

        run_lines = fake_uv.read_text(encoding="utf-8").splitlines()
        assert run_lines[0].split()[-2:] == ["mutmut", "run"], (
            "full runs should invoke mutmut without changed-module globs"
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

    @pytest.mark.parametrize("exit_code", [0, 3], ids=["success", "nonzero-exit"])
    @POSIX_SHIMS_ONLY
    def test_mutation_run_isolates_and_restores_environment(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        fake_uv: Path,
        exit_code: int,
    ) -> None:
        """Mutmut sees no workflow inputs and the caller environment is restored.

        Reproduces shared-actions#369: the caller sets
        ``INPUT_MODULE_PREFIX_STRIP=""``; without sanitisation that value
        reaches mutmut's in-process pytest baseline and breaks tests that
        read the ambient environment. A separate sentinel proves unrelated
        variables remain available to the baseline.
        """
        self._prepare(tmp_path, monkeypatch)
        input_values = {
            "INPUT_FILES": "src/mypkg/calc.py",
            "INPUT_MODULE_PREFIX_STRIP": "",
        }
        for name, value in input_values.items():
            monkeypatch.setenv(name, value)
            monkeypatch.setitem(local.env, name, value)
        monkeypatch.setenv("MUTMUT_TEST_SENTINEL", "keep-me")
        monkeypatch.setitem(local.env, "MUTMUT_TEST_SENTINEL", "keep-me")
        if exit_code:
            monkeypatch.setenv("FAKE_MUTMUT_RUN_EXIT", str(exit_code))
            monkeypatch.setitem(local.env, "FAKE_MUTMUT_RUN_EXIT", str(exit_code))

        if exit_code:
            with pytest.raises(SystemExit) as excinfo:
                run_mutmut.app([])
            assert excinfo.value.code == exit_code, (
                "mutmut's failure code should still propagate"
            )
        else:
            run_mutmut.app([])

        env_dump = (tmp_path / "uv-env.txt").read_text(encoding="utf-8")
        leaked = [line for line in env_dump.splitlines() if line.startswith("INPUT_")]
        assert not leaked, (
            f"no INPUT_* variable should reach the mutmut subprocess, found {leaked}"
        )
        assert "MUTMUT_TEST_SENTINEL=keep-me" in env_dump.splitlines(), (
            "non-INPUT_ environment variables should reach the mutmut subprocess"
        )
        for name, value in input_values.items():
            assert os.environ[name] == value, (
                f"{name} should be restored in the process environment"
            )
            assert local.env[name] == value, (
                f"{name} should be restored for later calls"
            )

    @pytest.mark.parametrize(
        "contents",
        [
            pytest.param("[tool.mutmut]\n", id="missing-source-paths"),
            pytest.param("[tool.mutmut]\nsource_paths = []\n", id="empty-source-paths"),
        ],
    )
    def test_invalid_scope_fails_before_test_only_short_circuit(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        fake_uv: Path,
        contents: str,
    ) -> None:
        """Invalid scope config fails before a changed-file no-op is selected."""
        self._prepare(tmp_path, monkeypatch)
        (tmp_path / "pyproject.toml").write_text(contents, encoding="utf-8")
        monkeypatch.setenv("INPUT_FILES", "tests/test_only.py")

        with pytest.raises(SystemExit) as excinfo:
            run_mutmut.app([])

        assert excinfo.value.code == 1, (
            "invalid source configuration must fail the step"
        )
        assert not fake_uv.exists(), (
            "invalid configuration must fail before invoking uv"
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
        monkeypatch.setenv(
            "INPUT_FILES", "src/mypkg/tests/test_post_turn_quality_stop_hook.py"
        )
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
