"""The sandbox's lifecycle and the readers' fallibility, at their boundaries.

A run that builds a sandbox must remove it again and say so when it cannot; a
reader that cannot inspect a path must name that rather than answer "absent".
These cases drive the real phase with one port replaced, so each defect has to
show up in a phase result rather than in an adapter method alone.
"""

from __future__ import annotations

import dataclasses as dc
import sys
import typing as typ
from pathlib import Path

from workflow_scripts.mutationpreflight import running
from workflow_scripts.mutationpreflight.check_boundary import run_checks_phase
from workflow_scripts.mutationpreflight.constants import MAX_CHECK_TIMEOUT_SECONDS
from workflow_scripts.mutationpreflight.models import Check, Declaration, PhaseResult
from workflow_scripts.mutationpreflight.repository import (
    FileDocuments,
    SandboxMaterializer,
)
from workflow_scripts.mutationpreflight.resolution import resolve
from workflow_scripts.tests.mutation_preflight_support import (
    Checkout,
    lock_document,
    repository,
)
from workflow_scripts.tests.test_mutation_preflight_repository_port import _run

if typ.TYPE_CHECKING:
    import pytest

    from workflow_scripts.mutationpreflight.copy_set import CopyContract
    from workflow_scripts.mutationpreflight.ports import Run


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


class TestSandboxOwnership:
    """The run removes a sandbox it built, and never the checkout it read."""

    @staticmethod
    def _recording_run(
        root: Path, *, refuse_removal: bool = False
    ) -> tuple[Run, list[Path]]:
        """Return a run over *root* whose sandbox records every discard."""
        discarded: list[Path] = []

        class RecordingSandbox:
            """Records what the run asks to remove, then delegates."""

            def materialize(
                self, checkout: Path, contract: CopyContract
            ) -> tuple[Path | None, tuple[str, ...]]:
                """Build the sandbox the production adapter would build."""
                return SandboxMaterializer().materialize(checkout, contract)

            def discard(self, sandbox: Path) -> str | None:
                """Record the sandbox the run asks to remove, or refuse."""
                discarded.append(sandbox)
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
        return run, discarded

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

    def test_a_materialised_sandbox_is_discarded_after_a_passing_check(
        self, tmp_path: Path
    ) -> None:
        """A sandbox the run created must not outlive a passing check."""
        root = repository(tmp_path / "repo", Checkout())
        run, discarded = self._recording_run(root)
        declaration = self._declaration(root, (sys.executable, "-c", "print(1)"))

        result = run_checks_phase(run, declaration)

        assert result.status == "pass", result
        assert len(discarded) == 1, (
            "a passing run must remove the sandbox it built exactly once"
        )

    def test_a_materialised_sandbox_is_discarded_after_a_failing_check(
        self, tmp_path: Path
    ) -> None:
        """A sandbox the run created must not outlive a failing check."""
        root = repository(tmp_path / "repo", Checkout())
        run, discarded = self._recording_run(root)
        declaration = self._declaration(
            root, (sys.executable, "-c", "raise SystemExit(1)")
        )

        result = run_checks_phase(run, declaration)

        assert result.status == "fail", result
        assert len(discarded) == 1, (
            "a failing run must still remove the sandbox it built"
        )

    def test_the_checkout_is_never_discarded(self, tmp_path: Path) -> None:
        """The cargo lane runs in the checkout, which the run does not own."""
        root = repository(tmp_path / "repo", Checkout())
        run, discarded = self._recording_run(root)

        result = run_checks_phase(run, _declaration(root))

        assert result.status == "pass", result
        assert discarded == [], "a checkout the run only read must never be removed"


class TestReadFailuresAreReported:
    """A filesystem the process may not inspect answers, rather than raising."""

    def test_an_unreadable_declaration_path_is_reported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A permission error on a declaration becomes the document's problem."""
        root = repository(tmp_path / "repo", Checkout())
        _refuse_path(monkeypatch, "exists", "mutation-preflight.toml")

        documents = FileDocuments().read_documents(
            root, (Path("pyproject.toml"), Path(".github") / "mutation-preflight.toml")
        )

        assert len(documents) == 2, "the readable document must still be returned"
        problem = documents[-1]
        assert problem.document is None
        assert "could not be inspected" in problem.problem, problem.problem

    def test_an_unreadable_lock_is_reported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A permission error on uv.lock becomes the lock's problem."""
        root = repository(
            tmp_path / "repo",
            Checkout(extra={"uv.lock": lock_document("0.3.1", 'registry = "x"')}),
        )
        _refuse_path(monkeypatch, "is_file", "uv.lock")

        packages, problem = FileDocuments().read_lock_packages(root)

        assert packages == []
        assert problem is not None, "an unreadable lock must report a problem"
        assert "could not be inspected" in problem, problem


class TestCleanupIsReported:
    """A sandbox that survives its cleanup is named rather than ignored."""

    def test_a_sandbox_that_cannot_be_removed_is_reported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failing removal returns the reason instead of discarding it."""
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()

        def refuse(*_args: object, **_kwargs: object) -> None:
            """Fail the removal as an unwritable directory would."""
            message = "Permission denied"
            raise PermissionError(13, message, str(sandbox))

        monkeypatch.setattr(sys.modules["shutil"], "rmtree", refuse)

        problem = SandboxMaterializer().discard(sandbox)

        assert problem is not None
        assert "could not be removed" in problem, problem


class TestCleanupReachesTheVerdict:
    """A sandbox the run cannot remove fails the checks phase."""

    def _surviving_sandbox_phase(
        self, tmp_path: Path, behaviour: str
    ) -> tuple[PhaseResult, list[Path]]:
        """Run a mutmut-lane phase whose sandbox refuses to be removed."""
        root = repository(tmp_path / "repo", Checkout())
        run, discarded = TestSandboxOwnership._recording_run(root, refuse_removal=True)
        declaration = _declaration(root, lane="mutmut", behaviour=behaviour)
        return run_checks_phase(_executable_run(run), declaration), discarded

    def test_a_surviving_sandbox_fails_the_checks_phase(self, tmp_path: Path) -> None:
        """The phase must not pass when its sandbox outlives it."""
        result, discarded = self._surviving_sandbox_phase(tmp_path, "print(1)")

        assert result.status == "fail", result
        assert len(discarded) == 1, "the run must have attempted the removal"
        assert any("could not be removed" in finding for finding in result.findings), (
            f"the finding must name the surviving sandbox: {result.findings}"
        )

    def test_a_cleanup_failure_keeps_a_check_failure(self, tmp_path: Path) -> None:
        """Both the failing check and the surviving sandbox are reported."""
        result, _ = self._surviving_sandbox_phase(tmp_path, "raise SystemExit(1)")

        assert result.status == "fail", result
        found = result.findings
        assert any("failed with exit code 1" in finding for finding in found), found
        assert any("could not be removed" in finding for finding in found), found


class TestDirectoryInspection:
    """An uninspectable cwd is named, not reported as a missing directory."""

    def test_an_uninspectable_cwd_names_the_inspection_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A PermissionError must not read as "is not a directory"."""
        _refuse_path(monkeypatch, "is_dir", "fixtures")

        root = repository(tmp_path / "repo", Checkout())
        (root / "fixtures").mkdir()
        run, _ = TestSandboxOwnership._recording_run(root)

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

        table, problem = FileDocuments().read_mutmut_configuration(root)

        assert table is None
        assert problem is not None
        assert "setup.cfg could not be read" in problem, problem

    def test_a_directory_named_uv_lock_is_reported(self, tmp_path: Path) -> None:
        """A present non-file must not be reported as an absent lock."""
        root = repository(tmp_path / "repo", Checkout())
        (root / "uv.lock").mkdir()

        packages, problem = FileDocuments().read_lock_packages(root)

        assert packages == []
        assert problem is not None
        assert "not a file" in problem, problem

    def test_a_directory_named_setup_cfg_is_reported(self, tmp_path: Path) -> None:
        """A directory where setup.cfg belongs is a refusal, not absence."""
        root = tmp_path / "repo"
        root.mkdir()
        (root / "setup.cfg").mkdir()

        table, problem = FileDocuments().read_mutmut_configuration(root)

        assert table is None
        assert problem is not None
        assert "not a file" in problem, problem
