"""The sandbox's lifecycle and the readers' fallibility, at their boundaries.

A run that builds a sandbox must remove it again and say so when it cannot; a
reader that cannot inspect a path must name that rather than answer "absent".
These cases drive the real phase with one port replaced, so each defect has to
show up in a phase result rather than in an adapter method alone.
"""

from __future__ import annotations

import dataclasses as dc
import os
import shutil
import sys
import tempfile
import typing as typ
from pathlib import Path

import pytest

from workflow_scripts.mutationpreflight import running
from workflow_scripts.mutationpreflight.check_boundary import run_checks_phase
from workflow_scripts.mutationpreflight.constants import MAX_CHECK_TIMEOUT_SECONDS
from workflow_scripts.mutationpreflight.copy_set import CopyContract
from workflow_scripts.mutationpreflight.models import Check, Declaration, PhaseResult
from workflow_scripts.mutationpreflight.repository import (
    CheckoutPaths,
    DeclarationDocuments,
    LockRecords,
    MutmutConfiguration,
    SandboxMaterializer,
)
from workflow_scripts.mutationpreflight.resolution import resolve
from workflow_scripts.tests.mutation_preflight_support import (
    POSIX_SYMLINKS_ONLY,
    Checkout,
    Outcome,
    lock_document,
    repository,
    write_file,
)
from workflow_scripts.tests.test_mutation_preflight_repository_port import _run

if typ.TYPE_CHECKING:
    from workflow_scripts.mutationpreflight.ports import Run

#: Permission bits restrain the process only where it is not privileged.
_PERMISSIONS_RESTRAIN: typ.Final[bool] = (
    sys.platform != "win32" and getattr(os, "geteuid", lambda: 1)() != 0
)
POSIX_PERMISSIONS_ONLY = pytest.mark.skipif(
    not _PERMISSIONS_RESTRAIN,
    reason="permission bits do not restrain a privileged or non-POSIX process",
)


def _refuse_path(monkeypatch: pytest.MonkeyPatch, method: str, named: str) -> None:
    """Make ``Path.<method>`` raise PermissionError for paths called *named*.

    The refusal stands in for a filesystem the process may not inspect, so a
    reader that folds absence and refusal together answers wrongly.
    """
    real = getattr(Path, method)

    def refuse(self: Path, *args: object, **kwargs: object) -> object:
        """Refuse to answer for the named path, as an unreadable one would."""
        if self.name == named:
            raise PermissionError(13, "Permission denied", str(self))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, method, refuse)


def _refuse_removal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every removal fail, as an unwritable sandbox would."""

    def refuse(*_args: object, **_kwargs: object) -> None:
        """Fail the removal as an unwritable directory would."""
        message = "Permission denied"
        raise PermissionError(13, message, "sandbox")

    monkeypatch.setattr(sys.modules["shutil"], "rmtree", refuse)


def _declaration(
    root: Path, *, lane: str = "cargo", behaviour: str = "print(1)", cwd: str = "."
) -> Declaration:
    """Return a one-check declaration for *root*, running *behaviour* from *cwd*."""
    return Declaration(
        source=root / "pyproject.toml",
        lane=lane,
        checks=(
            Check(
                name="collect",
                command=(sys.executable, "-c", behaviour),
                cwd=cwd,
                timeout_seconds=MAX_CHECK_TIMEOUT_SECONDS,
            ),
        ),
    )


def _executable_run(run: Run) -> Run:
    """Return *run* with the production runner and resolver wired in."""
    return dc.replace(
        run, ports=dc.replace(run.ports, runner=running.run_command, resolver=resolve)
    )


@dc.dataclass
class Recording:
    """The sandboxes a recording provider was asked to build and to remove."""

    created: list[Path] = dc.field(default_factory=list)
    discarded: list[Path] = dc.field(default_factory=list)

    def remove_survivors(self) -> None:
        """Remove every sandbox the run built, so a test leaves none behind."""
        for sandbox in self.created:
            shutil.rmtree(sandbox, ignore_errors=True)


class TestSandboxOwnership:
    """The run removes a sandbox it built, and never the checkout it read."""

    @staticmethod
    def _recording_run(
        root: Path, *, refuse_removal: bool = False
    ) -> tuple[Run, Recording]:
        """Return a run over *root* whose sandbox records every discard."""
        recording = Recording()

        class RecordingSandbox:
            """Records what the run asks to build and remove, then delegates."""

            def materialize(
                self, checkout: Path, contract: CopyContract
            ) -> tuple[Path | None, tuple[str, ...]]:
                """Build the sandbox the production adapter would build."""
                sandbox, problems = SandboxMaterializer().materialize(
                    checkout, contract
                )
                if sandbox is not None:
                    recording.created.append(sandbox)
                return sandbox, problems

            def discard(self, sandbox: Path) -> str | None:
                """Record the sandbox the run asks to remove, or refuse."""
                recording.discarded.append(sandbox)
                if refuse_removal:
                    return f"the check sandbox {str(sandbox)!r} could not be removed"
                return SandboxMaterializer().discard(sandbox)

        # The checks legitimately run commands, so this run uses the production
        # runner and resolver; only the sandbox is recorded.
        base = _run(root, root.parent / "out")
        run = dc.replace(
            base,
            ports=dc.replace(
                base.ports,
                sandbox=RecordingSandbox(),
                runner=running.run_command,
                resolver=resolve,
            ),
        )
        return run, recording

    def _declaration(self, root: Path, command: tuple[str, ...]) -> Declaration:
        """Return a mutmut-lane declaration with one check."""
        return Declaration(
            source=root / "pyproject.toml",
            lane="mutmut",
            checks=(
                Check(
                    name="collect",
                    command=command,
                    timeout_seconds=MAX_CHECK_TIMEOUT_SECONDS,
                ),
            ),
        )

    @pytest.mark.parametrize(
        ("behaviour", "expected"),
        [("print(1)", "pass"), ("raise SystemExit(1)", "fail")],
        ids=["passing", "failing"],
    )
    def test_a_materialised_sandbox_is_discarded_after_either_verdict(
        self, tmp_path: Path, behaviour: str, expected: str
    ) -> None:
        """A sandbox the run created must not outlive the check that ran in it."""
        root = repository(tmp_path / "repo", Checkout())
        run, recording = self._recording_run(root)
        declaration = self._declaration(root, (sys.executable, "-c", behaviour))

        result = run_checks_phase(run, declaration)

        assert result.status == expected, result
        assert recording.created, "the mutmut lane must build a sandbox to run in"
        assert recording.created != [root], (
            "the sandbox must be a copy, not the checkout it was built from"
        )
        assert recording.discarded == recording.created, (
            "the run must remove the exact sandbox it built, exactly once"
        )

    def test_the_checkout_is_never_discarded(self, tmp_path: Path) -> None:
        """The cargo lane runs in the checkout, which the run does not own."""
        root = repository(tmp_path / "repo", Checkout())
        run, recording = self._recording_run(root)

        result = run_checks_phase(run, _declaration(root))

        assert result.status == "pass", result
        assert recording.created == [], "the cargo lane needs no sandbox copy"
        assert recording.discarded == [], (
            "a checkout the run only read must never be removed"
        )

    def test_what_the_run_built_is_removed_at_the_checkout_path_too(
        self, tmp_path: Path
    ) -> None:
        """Ownership, not a path difference, decides what the run removes."""
        root = repository(tmp_path / "repo", Checkout())
        discarded: list[Path] = []

        class SamePlaceSandbox:
            """Reports the checkout itself as the sandbox it built for the run."""

            def materialize(
                self, checkout: Path, contract: CopyContract
            ) -> tuple[Path | None, tuple[str, ...]]:
                """Hand back the checkout, as a provider that copied nothing."""
                return checkout, ()

            def discard(self, sandbox: Path) -> str | None:
                """Record the removal and refuse it, so the fixture survives."""
                discarded.append(sandbox)
                return f"the check sandbox {str(sandbox)!r} could not be removed"

        base = _run(root, root.parent / "out")
        run = dc.replace(
            base,
            ports=dc.replace(
                base.ports,
                sandbox=SamePlaceSandbox(),
                runner=running.run_command,
                resolver=resolve,
            ),
        )

        result = run_checks_phase(run, _declaration(root, lane="mutmut"))

        assert result.status == "fail", result
        assert discarded == [root], (
            "the sandbox the run built must be removed even where the checkout is"
        )
        assert root.is_dir(), "a refused removal must not touch the checkout"


class TestReadFailuresAreReported:
    """A filesystem the process may not inspect answers, rather than raising."""

    def test_an_unreadable_declaration_path_is_reported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A refusal to inspect a declaration becomes the document's problem."""
        root = repository(tmp_path / "repo", Checkout())
        _refuse_path(monkeypatch, "stat", "mutation-preflight.toml")

        documents = DeclarationDocuments().read_documents(
            root, (Path("pyproject.toml"), Path(".github") / "mutation-preflight.toml")
        )

        assert len(documents) == 2, "the readable document must still be returned"
        problem = documents[-1]
        assert problem.document is None
        assert "could not be inspected" in problem.problem, problem.problem

    def test_an_unreadable_lock_is_reported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A refusal to inspect the lock is not the lock's absence."""
        root = repository(
            tmp_path / "repo",
            Checkout(extra={"uv.lock": lock_document("0.3.1", 'registry = "x"')}),
        )
        _refuse_path(monkeypatch, "stat", "uv.lock")

        packages, problem = LockRecords().read_lock_packages(root)

        assert packages == []
        assert problem is not None, "an unreadable lock must report a problem"
        assert "could not be inspected" in problem, problem

    @POSIX_PERMISSIONS_ONLY
    def test_an_unsearchable_checkout_is_reported_not_read_as_absent(
        self, tmp_path: Path
    ) -> None:
        """A real permission refusal must not read as an absent lock."""
        root = repository(tmp_path / "repo", Checkout())
        root.chmod(0o000)
        try:
            packages, problem = LockRecords().read_lock_packages(root)
        finally:
            root.chmod(0o700)

        assert packages == []
        assert problem is not None, "a checkout it may not search must be reported"
        assert "could not be inspected" in problem, problem


class TestCleanupIsReported:
    """A sandbox that survives its cleanup is named rather than ignored."""

    def test_a_sandbox_that_cannot_be_removed_is_reported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failing removal returns the reason instead of discarding it."""
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        _refuse_removal(monkeypatch)

        problem = SandboxMaterializer().discard(sandbox)

        assert problem is not None
        assert "could not be removed" in problem, problem


class TestMaterialisationFailures:
    """A sandbox that could not be built is still removed, and the rest said."""

    #: A one-entry contract whose copy is replaced by the failure under test.
    CONTRACT: typ.Final[CopyContract] = CopyContract(paths=("conftest.py",), globs=())

    def test_a_failed_copy_names_the_sandbox_it_could_not_remove(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An I/O failure while copying reports both the copy and the removal."""
        root = repository(tmp_path / "repo", Checkout())
        destination = tmp_path / "sandbox"

        def explode(*_args: object, **_kwargs: object) -> str | None:
            """Fail the copy as a full disk would."""
            message = "No space left on device"
            raise OSError(28, message)

        with monkeypatch.context() as patch:
            patch.setattr(tempfile, "mkdtemp", lambda **_kwargs: str(destination))
            patch.setattr(SandboxMaterializer, "_copy_contract", explode)
            _refuse_removal(patch)

            sandbox, problems = SandboxMaterializer().materialize(root, self.CONTRACT)

        shutil.rmtree(destination, ignore_errors=True)
        assert sandbox is None
        assert any("could not be materialized" in item for item in problems), problems
        assert any("could not be removed" in item for item in problems), problems

    def test_a_refused_copy_entry_names_the_sandbox_it_could_not_remove(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A refused entry reports the refusal and the sandbox that survived."""
        root = repository(tmp_path / "repo", Checkout())
        destination = tmp_path / "sandbox"
        contract = CopyContract(paths=("../outside",), globs=())

        with monkeypatch.context() as patch:
            patch.setattr(tempfile, "mkdtemp", lambda **_kwargs: str(destination))
            _refuse_removal(patch)

            sandbox, problems = SandboxMaterializer().materialize(root, contract)

        shutil.rmtree(destination, ignore_errors=True)
        assert sandbox is None
        assert any("outside" in item for item in problems), problems
        assert any("could not be removed" in item for item in problems), problems


class TestCleanupReachesTheVerdict:
    """A sandbox the run cannot remove fails the checks phase."""

    def _surviving_sandbox_phase(
        self, tmp_path: Path, behaviour: str
    ) -> tuple[PhaseResult, int]:
        """Run a mutmut-lane phase whose sandbox refuses to be removed."""
        root = repository(tmp_path / "repo", Checkout())
        run, recording = TestSandboxOwnership._recording_run(root, refuse_removal=True)
        declaration = _declaration(root, lane="mutmut", behaviour=behaviour)
        try:
            result = run_checks_phase(_executable_run(run), declaration)
            return result, len(recording.discarded)
        finally:
            recording.remove_survivors()

    def test_a_surviving_sandbox_fails_the_checks_phase(self, tmp_path: Path) -> None:
        """The phase must not pass when its sandbox outlives it."""
        result, attempts = self._surviving_sandbox_phase(tmp_path, "print(1)")

        assert result.status == "fail", result
        assert attempts == 1, "the run must have attempted the removal"
        assert any("could not be removed" in finding for finding in result.findings), (
            f"the finding must name the surviving sandbox: {result.findings}"
        )

    def test_a_cleanup_failure_keeps_a_check_failure(self, tmp_path: Path) -> None:
        """Both the failing check and the surviving sandbox are reported."""
        result, _attempts = self._surviving_sandbox_phase(
            tmp_path, "raise SystemExit(1)"
        )

        assert result.status == "fail", result
        found = result.findings
        assert any("failed with exit code 1" in finding for finding in found), found
        assert any("could not be removed" in finding for finding in found), found


class TestCleanupReachesTheCommandLine:
    """A sandbox the run cannot remove reaches the command line's findings."""

    def test_a_surviving_sandbox_is_published_on_standard_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        run_preflight: typ.Callable[..., Outcome],
    ) -> None:
        """A cleanup finding must reach the reader, not stay in the phase."""
        root = repository(
            tmp_path / "repo",
            Checkout(
                manifest=(
                    "[[checks]]\n"
                    'name = "collect"\n'
                    f"command = ['{sys.executable}', '-c', 'print(1)']\n"
                )
            ),
        )
        destination = tmp_path / "sandbox"

        with monkeypatch.context() as patch:
            patch.setattr(tempfile, "mkdtemp", lambda **_kwargs: str(destination))
            _refuse_removal(patch)

            outcome = run_preflight(root, project_python="3.13")

        shutil.rmtree(destination, ignore_errors=True)
        assert outcome.exit_code == 1, outcome.stdout
        assert outcome.result == "fail"
        assert "checks: the check sandbox" in outcome.stderr, outcome.stderr
        assert "could not be removed" in outcome.stderr, outcome.stderr


class TestDirectoryInspection:
    """An uninspectable cwd is named, not reported as a missing directory."""

    def test_the_port_names_a_refused_inspection(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A refusal to inspect a directory is a problem, not a "no"."""
        _refuse_path(monkeypatch, "stat", "fixtures")
        path = tmp_path / "fixtures"
        path.mkdir()

        is_directory, problem = CheckoutPaths().is_directory(path)

        assert is_directory is False
        assert problem is not None, "an uninspectable directory must report a problem"
        assert "could not be inspected" in problem, problem

    def test_an_uninspectable_cwd_names_the_inspection_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A PermissionError must not read as "is not a directory"."""
        _refuse_path(monkeypatch, "stat", "fixtures")

        root = repository(tmp_path / "repo", Checkout())
        (root / "fixtures").mkdir()
        run, _recording = TestSandboxOwnership._recording_run(root)

        result = run_checks_phase(
            _executable_run(run), _declaration(root, cwd="fixtures")
        )

        assert result.status == "fail", result
        found = result.findings
        assert any("could not be inspected" in finding for finding in found), found
        assert not any("is not a directory" in finding for finding in found), found


class TestPresentButUnusableConfiguration:
    """A configuration that exists but cannot be read is refused, not absent."""

    def test_a_setup_cfg_that_cannot_be_opened_is_reported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An open failure must not read as "neither file is present"."""
        root = tmp_path / "repo"
        root.mkdir()
        (root / "setup.cfg").write_text(
            "[mutmut]\nsource_paths = src\n", encoding="utf-8"
        )
        real_read_text = Path.read_text

        def refuse(self: Path, *args: object, **kwargs: object) -> str:
            """Refuse to open setup.cfg as an unreadable file would."""
            if self.name == "setup.cfg":
                raise PermissionError(13, "Permission denied", str(self))
            return real_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", refuse)

        table, problem = MutmutConfiguration().read_mutmut_configuration(root)

        assert table is None
        assert problem is not None
        assert "setup.cfg could not be read" in problem, problem

    def test_a_directory_named_uv_lock_is_reported(self, tmp_path: Path) -> None:
        """A present non-file must not be reported as an absent lock."""
        root = repository(tmp_path / "repo", Checkout())
        (root / "uv.lock").mkdir()

        packages, problem = LockRecords().read_lock_packages(root)

        assert packages == []
        assert problem is not None
        assert "not a file" in problem, problem

    def test_a_directory_named_setup_cfg_is_reported(self, tmp_path: Path) -> None:
        """A directory where setup.cfg belongs is a refusal, not absence."""
        root = tmp_path / "repo"
        root.mkdir()
        (root / "setup.cfg").mkdir()

        table, problem = MutmutConfiguration().read_mutmut_configuration(root)

        assert table is None
        assert problem is not None
        assert "not a file" in problem, problem

    @pytest.mark.parametrize(
        "with_setup_cfg", [False, True], ids=["alone", "with-setup-cfg"]
    )
    def test_a_non_table_tool_mutmut_is_reported(
        self, tmp_path: Path, *, with_setup_cfg: bool
    ) -> None:
        """Mutmut selects a present value and fails, so it is not absence."""
        root = tmp_path / "repo"
        root.mkdir()
        write_file(
            root,
            "pyproject.toml",
            '[project]\nname = "example"\n\n[tool]\nmutmut = "invalid"\n',
        )
        if with_setup_cfg:
            write_file(root, "setup.cfg", "[mutmut]\nsource_paths = src\n")

        table, problem = MutmutConfiguration().read_mutmut_configuration(root)

        assert table is None, "the invalid value must not yield a copy set"
        assert problem is not None, "a non-table section must be reported"
        assert "not a table" in problem, problem
        assert "is present, so what mutmut copies" not in problem, (
            "a present value must not be reported as a missing section"
        )

    def test_an_absent_tool_mutmut_falls_back_to_setup_cfg(
        self, tmp_path: Path
    ) -> None:
        """An absent section is the case mutmut itself falls back on."""
        root = tmp_path / "repo"
        root.mkdir()
        write_file(root, "pyproject.toml", '[project]\nname = "example"\n')
        write_file(root, "setup.cfg", "[mutmut]\nsource_paths = src\n")

        table, problem = MutmutConfiguration().read_mutmut_configuration(root)

        assert problem is None, problem
        assert table is not None, "the fallback must supply the configuration"
        assert table["source_paths"] == ["src"], table

    @POSIX_SYMLINKS_ONLY
    def test_a_dangling_lock_is_reported_not_absent(self, tmp_path: Path) -> None:
        """A link whose target is missing is present and unreadable, not absent."""
        root = repository(tmp_path / "repo", Checkout())
        (root / "uv.lock").symlink_to(root / "missing.lock")

        packages, problem = LockRecords().read_lock_packages(root)

        assert packages == []
        assert problem is not None, "a dangling link must be reported, not skipped"
        assert "not a file" in problem, problem
