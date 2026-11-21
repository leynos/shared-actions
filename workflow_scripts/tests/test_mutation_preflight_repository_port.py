"""The repository port: every judgement reads the checkout through it.

A phase receives parsed documents, located paths and records from
``RepositoryFiles`` and never touches a path itself. The stand-in here answers
every question with one refusal and touches nothing, so the valid checkout it
is given is exactly the evidence a phase would contradict if it read the
filesystem directly.
"""

from __future__ import annotations

import dataclasses as dc
import sys
import typing as typ

from workflow_scripts.mutationpreflight import running
from workflow_scripts.mutationpreflight.checks import run_checks_phase
from workflow_scripts.mutationpreflight.constants import MAX_CHECK_TIMEOUT_SECONDS
from workflow_scripts.mutationpreflight.interpreters import run_interpreter_phase
from workflow_scripts.mutationpreflight.models import (
    Check,
    Declaration,
    NativeRequirement,
    Runtimes,
)
from workflow_scripts.mutationpreflight.native import judge_native
from workflow_scripts.mutationpreflight.ports import (
    Document,
    LocatedPath,
    Run,
)
from workflow_scripts.mutationpreflight.repository import FileRepository
from workflow_scripts.mutationpreflight.sandbox import run_sandbox_phase
from workflow_scripts.tests.mutation_preflight_support import (
    Checkout,
    lock_document,
    repository,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

    from workflow_scripts.mutationpreflight.copy_set import CopyContract


def _refuse(*_args: object, **_kwargs: object) -> typ.NoReturn:
    """Fail the test: a query phase has no command to run."""
    message = "a query phase must not run a command"
    raise AssertionError(message)


def _run(root: Path, output_dir: Path) -> Run:
    """Return a run whose runner fails if any phase reaches for a command."""
    return Run(
        root=root,
        runtimes=Runtimes(helper="3.13.5", project="3.13"),
        output_dir=output_dir,
        mutmut_version="3.7.0",
        ambient={},
        summary_path="",
        abi3="unset",
        rule=None,
        rule_problem="the shared rule was not loaded",
        files=FileRepository(),
        runner=_refuse,
    )


class RefusingRepository:
    """A repository port that refuses every question without a filesystem call.

    It answers each operation with one reason and touches no path. The checkout
    it is given is a valid one whose own files would pass every phase, so a
    phase that read the checkout directly would report a verdict this stand-in
    never supplied.
    """

    #: The one reason every operation gives.
    reason: typ.Final[str] = "the repository stand-in refuses to read the checkout"

    def read_documents(
        self, root: Path, relatives: tuple[Path, ...]
    ) -> tuple[Document, ...]:
        """Return each asked-for document as present but unreadable."""
        return tuple(
            Document(root / relative, None, self.reason) for relative in relatives
        )

    def read_lock_packages(
        self, root: Path
    ) -> tuple[list[dict[str, object]], str | None]:
        """Refuse to read the lock."""
        return [], self.reason

    def read_mutmut_configuration(
        self, root: Path
    ) -> tuple[dict[str, object] | None, str | None]:
        """Refuse to read the mutmut configuration."""
        return None, self.reason

    def locate(
        self, root: Path, declared: str
    ) -> tuple[LocatedPath | None, str | None]:
        """Refuse to resolve a declared path."""
        return None, self.reason

    def is_directory(self, path: Path) -> bool:
        """Refuse to establish that a path is a directory."""
        return False

    def materialize(
        self, root: Path, contract: CopyContract
    ) -> tuple[Path | None, tuple[str, ...]]:
        """Refuse to build a sandbox."""
        return None, (self.reason,)

    def discard(self, sandbox: Path) -> None:
        """Refuse to remove a sandbox; there is none to remove."""

    def write_check_logs(
        self, directory: Path, stem: str, name: str, outcome: object
    ) -> str | None:
        """Refuse to write a check's logs."""
        return self.reason

    def append_summary(self, path: Path, rendered: str) -> str | None:
        """Refuse to append the job summary."""
        return self.reason

    def write_report(self, output_dir: Path, document: dict[str, object]) -> str | None:
        """Refuse to write the report."""
        return self.reason


class TestRepositoryBoundary:
    """Every judgement reads the checkout through the repository port."""

    def test_the_judgement_phases_read_only_through_the_port(
        self, tmp_path: Path
    ) -> None:
        """A refusing port leaves the phases without the checkout's own answers."""
        root = repository(
            tmp_path / "repo",
            Checkout(
                requires_python=">=3.12",
                also_copy=("conftest.py", "README.md"),
                extra={
                    "README.md": "# example\n",
                    "uv.lock": lock_document("0.3.1", 'registry = "x"'),
                },
            ),
        )
        output = tmp_path / "out"
        declaration = Declaration(
            source=root / "pyproject.toml",
            lane="cargo",
            sandbox_paths=("README.md",),
            native=NativeRequirement(package="tei-rapporteur"),
            checks=(
                Check(
                    name="collect",
                    command=(sys.executable, "-c", "print(1)"),
                    timeout_seconds=MAX_CHECK_TIMEOUT_SECONDS,
                ),
            ),
        )
        real = _run(root, output)
        # The checkout really does satisfy each phase: read directly, the
        # interpreter, lock and sandbox path all hold.
        assert run_interpreter_phase(real).status != "fail"
        assert judge_native(real, declaration).status != "fail"
        assert run_sandbox_phase(real, declaration).status != "fail"
        # A check legitimately runs a command, so the real comparison uses the
        # production runner rather than the query-phase runner that refuses.
        executable = dc.replace(real, runner=running.run_command)
        assert run_checks_phase(executable, declaration).status == "pass"

        refusing = dc.replace(real, files=RefusingRepository())
        results = [
            run_interpreter_phase(refusing),
            judge_native(refusing, declaration),
            run_sandbox_phase(refusing, declaration),
            run_checks_phase(refusing, declaration),
        ]

        assert [result.status for result in results] == [
            "unknown",
            "unknown",
            "fail",
            "fail",
        ], "a refusing port must leave every phase unable to read the checkout"
        for result in results:
            reported = " ".join((*result.findings, str(result.detail)))
            assert RefusingRepository.reason in reported, (
                f"{result.name} must report the port's refusal: {result}"
            )
