"""Unit tests for the cargo-mutants run wrapper script."""

from __future__ import annotations

import stat
import sys
import typing as typ

import pytest
from plumbum import local

from workflow_scripts import mutation_run_cargo as run_cargo

if typ.TYPE_CHECKING:
    from pathlib import Path

# The reusable workflows only run on ubuntu-latest; the fake tool shims
# are POSIX shell scripts, so Windows falls through to the real tools.
POSIX_SHIMS_ONLY = pytest.mark.skipif(
    sys.platform == "win32", reason="fake cargo helper emits POSIX sh"
)


class TestBuildArguments:
    """Construction of the cargo-mutants argument list."""

    def test_defaults_produce_minimal_invocation(self) -> None:
        """Defaults yield an unscoped, unsharded root run."""
        assert run_cargo.build_arguments(run_cargo.MutantsInvocation()) == [
            "mutants",
            "--in-place",
            "--timeout-multiplier",
            "3",
        ]

    def test_files_become_repeated_file_arguments(self) -> None:
        """Each scoped file adds a ``--file`` argument."""
        arguments = run_cargo.build_arguments(
            run_cargo.MutantsInvocation(files="src/a.rs src/b.rs")
        )
        assert arguments[-4:] == ["--file", "src/a.rs", "--file", "src/b.rs"]

    def test_shard_and_dir_and_excludes(self) -> None:
        """Sharding, target dir, and exclude globs are all emitted."""
        arguments = run_cargo.build_arguments(
            run_cargo.MutantsInvocation(
                shard=2,
                shard_count=6,
                target_dir="testkit",
                exclude_globs="src/examples.rs, src/test_helpers.rs",
            )
        )
        assert arguments[4:6] == ["--dir", "testkit"]
        assert arguments[6:8] == ["--shard", "2/6"]
        assert arguments[-4:] == [
            "--exclude",
            "src/examples.rs",
            "--exclude",
            "src/test_helpers.rs",
        ]

    def test_extra_args_are_shell_lexed(self) -> None:
        """Extra arguments append verbatim after shell lexing."""
        arguments = run_cargo.build_arguments(
            run_cargo.MutantsInvocation(extra_args="--all-features -v")
        )
        assert arguments[-2:] == ["--all-features", "-v"]


class TestInterpretExitCode:
    """Classification of cargo-mutants exit codes."""

    @pytest.mark.parametrize("code", [0, 2, 3])
    def test_informative_codes_succeed(self, code: int) -> None:
        """Missed mutants and timeouts are informative outcomes."""
        success, meaning = run_cargo.interpret_exit_code(code)
        assert success
        assert meaning

    @pytest.mark.parametrize("code", [1, 4, 70, 99])
    def test_fault_codes_fail(self, code: int) -> None:
        """Usage errors, failing baselines, and unknowns are faults."""
        success, meaning = run_cargo.interpret_exit_code(code)
        assert not success
        assert meaning


@pytest.fixture
def fake_cargo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Install a fake ``cargo`` on PATH that records arguments.

    The fake writes its arguments to ``cargo-args.txt`` and exits with the
    code named by the ``FAKE_CARGO_EXIT`` environment variable.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    args_file = tmp_path / "cargo-args.txt"
    script = bin_dir / "cargo"
    script.write_text(
        f'#!/bin/sh\nprintf \'%s\\n\' "$@" > "{args_file}"\n'
        'exit "${FAKE_CARGO_EXIT:-0}"\n',
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}:{local.env['PATH']}")
    monkeypatch.setitem(local.env, "PATH", f"{bin_dir}:{local.env['PATH']}")
    return args_file


class TestMainEntry:
    """End-to-end behaviour of the CLI entry point."""

    @POSIX_SHIMS_ONLY
    def test_informative_exit_is_success(
        self,
        fake_cargo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Exit code 2 (missed mutants) does not raise."""
        monkeypatch.setenv("FAKE_CARGO_EXIT", "2")
        monkeypatch.setitem(local.env, "FAKE_CARGO_EXIT", "2")
        monkeypatch.setenv("INPUT_FILES", "src/lib.rs")
        run_cargo.app([])
        recorded = fake_cargo.read_text(encoding="utf-8").split()
        assert recorded[0] == "mutants"
        assert recorded[-2:] == ["--file", "src/lib.rs"]

    @POSIX_SHIMS_ONLY
    def test_genuine_fault_propagates_exit_code(
        self,
        fake_cargo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Exit code 4 (failing baseline) fails with the same code."""
        monkeypatch.setenv("FAKE_CARGO_EXIT", "4")
        monkeypatch.setitem(local.env, "FAKE_CARGO_EXIT", "4")
        with pytest.raises(SystemExit) as excinfo:
            run_cargo.app([])
        assert excinfo.value.code == 4
        assert fake_cargo.read_text(encoding="utf-8")

    def test_invalid_shard_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A shard index outside the shard count is a usage error."""
        monkeypatch.setenv("INPUT_SHARD", "6")
        monkeypatch.setenv("INPUT_SHARD_COUNT", "6")
        with pytest.raises(SystemExit) as excinfo:
            run_cargo.app([])
        assert excinfo.value.code == 1
