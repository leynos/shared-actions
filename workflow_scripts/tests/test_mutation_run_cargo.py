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


class TestCountEnumeratedMutants:
    """Reading the inventory cargo-mutants leaves behind."""

    def test_it_counts_the_listed_mutants(self, tmp_path: Path) -> None:
        """The inventory is a JSON list, one entry per mutant."""
        inventory = tmp_path / "mutants.out" / "mutants.json"
        inventory.parent.mkdir(parents=True)
        inventory.write_text('[{"function": "a"}, {"function": "b"}]', encoding="utf-8")

        assert run_cargo.count_enumerated_mutants(str(tmp_path)) == 2, (
            "each inventory entry is one mutant"
        )

    def test_an_empty_inventory_counts_zero(self, tmp_path: Path) -> None:
        """An empty list is the shape of a run that found nothing."""
        inventory = tmp_path / "mutants.out" / "mutants.json"
        inventory.parent.mkdir(parents=True)
        inventory.write_text("[]", encoding="utf-8")

        assert run_cargo.count_enumerated_mutants(str(tmp_path)) == 0, (
            "an empty list is a run that enumerated nothing"
        )

    @pytest.mark.parametrize(
        ("content", "reason"),
        [
            (None, "absent"),
            ("not json", "unparseable"),
            ('{"total": 0}', "not-a-list"),
        ],
        ids=["absent", "unparseable", "not-a-list"],
    )
    def test_an_unreadable_inventory_is_unknown_not_zero(
        self, tmp_path: Path, content: str | None, reason: str
    ) -> None:
        """Unknown must never be confused with zero.

        A cargo-mutants that stops writing the file, or writes it
        elsewhere, would otherwise fail every caller's run at once.
        """
        if content is not None:
            inventory = tmp_path / "mutants.out" / "mutants.json"
            inventory.parent.mkdir(parents=True)
            inventory.write_text(content, encoding="utf-8")

        assert run_cargo.count_enumerated_mutants(str(tmp_path)) is None, (
            f"an {reason} inventory must read as unknown, never as zero"
        )


class TestClassifyEmptyRun:
    """Separating a clean pass from an empty one."""

    def test_a_run_with_mutants_keeps_its_meaning(self) -> None:
        """The ordinary success path is left alone."""
        success, meaning = run_cargo.classify_empty_run(
            meaning="all mutants caught", enumerated=7, sharded=False, allowed=False
        )

        assert success, "a populated run must stay a pass"
        assert meaning == "all mutants caught", meaning

    def test_an_unknown_inventory_keeps_its_meaning(self) -> None:
        """Not knowing is not the same as knowing there were none."""
        success, meaning = run_cargo.classify_empty_run(
            meaning="all mutants caught", enumerated=None, sharded=False, allowed=False
        )

        assert success, "an unreadable inventory must not fail the run"
        assert meaning == "all mutants caught", meaning

    def test_an_empty_unsharded_run_fails(self, capsys: pytest.CaptureFixture) -> None:
        """The vacuous pass, which is the whole point of this change."""
        success, meaning = run_cargo.classify_empty_run(
            meaning="all mutants caught", enumerated=0, sharded=False, allowed=False
        )

        assert not success, "an empty unsharded run must fail the step"
        assert meaning == run_cargo.NO_MUTANTS, meaning
        assert "::error title=Mutation testing::" in capsys.readouterr().out, (
            "the failure must be annotated, not merely returned"
        )

    def test_an_empty_shard_is_ordinary(self) -> None:
        """Fewer mutants than shards leaves some shards with nothing."""
        success, meaning = run_cargo.classify_empty_run(
            meaning="all mutants caught", enumerated=0, sharded=True, allowed=False
        )

        assert success, "an empty shard is ordinary and must not fail"
        assert meaning == f"{run_cargo.NO_MUTANTS} in this shard", meaning

    def test_an_empty_run_the_caller_expects_is_reported_not_hidden(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """Opting out changes the verdict, never the report.

        The outcome still says no mutants were found, so a caller who set
        the flag once cannot later mistake an empty lane for a working one.
        """
        success, meaning = run_cargo.classify_empty_run(
            meaning="all mutants caught", enumerated=0, sharded=False, allowed=True
        )

        assert success, "the opt-out must change the verdict"
        assert meaning == f"{run_cargo.NO_MUTANTS} (allowed)", meaning
        assert "::notice title=Mutation testing::" in capsys.readouterr().out, (
            "the opt-out must still announce that nothing was found"
        )


class TestEmptyRunEndToEnd:
    """The empty run through the CLI entry point."""

    @staticmethod
    def _write_inventory(tmp_path: Path, entries: str) -> None:
        inventory = tmp_path / "mutants.out" / "mutants.json"
        inventory.parent.mkdir(parents=True, exist_ok=True)
        inventory.write_text(entries, encoding="utf-8")

    @POSIX_SHIMS_ONLY
    def test_an_empty_run_fails_the_step(
        self,
        fake_cargo: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """cargo-mutants exits 0; the step must not."""
        self._write_inventory(tmp_path, "[]")
        monkeypatch.chdir(tmp_path)

        with pytest.raises(SystemExit) as excinfo:
            run_cargo.app([])

        assert excinfo.value.code == 1, excinfo.value.code
        assert fake_cargo.read_text(encoding="utf-8"), "cargo must have run"

    @POSIX_SHIMS_ONLY
    def test_an_empty_run_passes_when_the_caller_allows_it(
        self,
        fake_cargo: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """The opt-out reaches the classifier from the environment."""
        self._write_inventory(tmp_path, "[]")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("INPUT_ALLOW_NO_MUTANTS", "true")

        run_cargo.app([])

        output = capsys.readouterr().out
        assert "mutation_cargo_outcome=no mutants found (allowed)" in output, output
        assert fake_cargo.read_text(encoding="utf-8")

    @POSIX_SHIMS_ONLY
    def test_a_populated_run_still_passes(
        self,
        fake_cargo: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """The change must not redden a lane that is doing its job."""
        self._write_inventory(tmp_path, '[{"function": "a"}]')
        monkeypatch.chdir(tmp_path)

        run_cargo.app([])

        output = capsys.readouterr().out
        assert "mutation_cargo_outcome=all mutants caught" in output, output
        assert fake_cargo.read_text(encoding="utf-8"), "cargo must have run"


class TestResolveOutputDir:
    """Finding where cargo-mutants will write its inventory."""

    def test_it_defaults_to_the_crate_directory(self) -> None:
        """With no override, mutants.out sits beside the crate."""
        assert run_cargo.resolve_output_dir("crates/thing", ["mutants"]) == (
            "crates/thing"
        ), "the crate directory is the default output location"

    @pytest.mark.parametrize(
        "arguments",
        [
            ["mutants", "--output", "out/elsewhere"],
            ["mutants", "--output=out/elsewhere"],
        ],
        ids=["separate", "joined"],
    )
    def test_it_honours_an_output_override(self, arguments: list[str]) -> None:
        """A caller can move mutants.out through extra-args.

        Reading the default location regardless would find no inventory,
        report it as unknown, and hand the empty run back its clean pass.
        """
        assert run_cargo.resolve_output_dir(".", arguments) == "out/elsewhere", (
            f"--output must be honoured; arguments were {arguments!r}"
        )

    @pytest.mark.parametrize(
        "arguments",
        [
            ["mutants", "--output", "out/first", "--output", "out/second"],
            ["mutants", "--output=out/first", "--output=out/second"],
            ["mutants", "--output", "out/first", "--output=out/second"],
            ["mutants", "--output=out/first", "--output", "out/second"],
        ],
        ids=[
            "both-separate",
            "both-joined",
            "separate-then-joined",
            "joined-then-separate",
        ],
    )
    def test_the_last_override_wins(self, arguments: list[str]) -> None:
        """As it does for cargo-mutants itself.

        Each spelling is covered on both sides, because a resolver that
        kept the first of one form and the last of the other would pass a
        single mixed case while still reading the wrong directory.
        """
        assert run_cargo.resolve_output_dir(".", arguments) == "out/second", (
            f"the last --output must win; arguments were {arguments!r}"
        )


class TestEmptyRunWithRelocatedOutput:
    """The inventory must be found where the caller put it."""

    @POSIX_SHIMS_ONLY
    def test_an_empty_run_still_fails_when_output_is_relocated(
        self,
        fake_cargo: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The regression Codex found: --output must not restore the pass."""
        elsewhere = tmp_path / "elsewhere"
        inventory = elsewhere / "mutants.out" / "mutants.json"
        inventory.parent.mkdir(parents=True)
        inventory.write_text("[]", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("INPUT_EXTRA_ARGS", f"--output {elsewhere}")

        with pytest.raises(SystemExit) as excinfo:
            run_cargo.app([])

        assert excinfo.value.code == 1, excinfo.value.code
        assert fake_cargo.read_text(encoding="utf-8"), "cargo must have run"


class TestUnreadableInventoryIsAnnounced:
    """Unknown must be visible, not merely safe."""

    def test_a_missing_inventory_warns_and_reports_a_metric(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """A cargo-mutants that stopped writing the file must be findable.

        Unknown keeps the run passing, which is right, but silently
        restoring the vacuous pass across every consumer is not.
        """
        assert run_cargo.count_enumerated_mutants(str(tmp_path)) is None, (
            "a missing inventory is unknown"
        )

        output = capsys.readouterr().out
        assert "::warning title=Mutation testing::" in output, output
        assert "mutation_cargo_inventory=unreadable" in output, output

    def test_a_readable_inventory_reports_its_own_metric(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """One bounded outcome per path, so the series can be aggregated."""
        inventory = tmp_path / "mutants.out" / "mutants.json"
        inventory.parent.mkdir(parents=True)
        inventory.write_text("[]", encoding="utf-8")

        assert run_cargo.count_enumerated_mutants(str(tmp_path)) == 0, "empty"

        output = capsys.readouterr().out
        assert "mutation_cargo_inventory=readable" in output, output
        assert "::warning" not in output, output
