"""The repository port: every judgement reads the checkout through it.

A phase receives parsed documents, located paths and records from
the repository ports and never touches a path itself. The stand-in here answers
every question with one refusal and touches nothing, so the valid checkout it
is given is exactly the evidence a phase would contradict if it read the
filesystem directly.
"""

from __future__ import annotations

import dataclasses as dc
import os
import sys
import typing as typ

from workflow_scripts.mutationpreflight import running
from workflow_scripts.mutationpreflight.check_boundary import run_checks_phase
from workflow_scripts.mutationpreflight.commands import run_commands_phase
from workflow_scripts.mutationpreflight.constants import MAX_CHECK_TIMEOUT_SECONDS
from workflow_scripts.mutationpreflight.interpreters import run_interpreter_phase
from workflow_scripts.mutationpreflight.models import (
    Check,
    CommandRequirement,
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
from workflow_scripts.mutationpreflight.probing import probe_native
from workflow_scripts.mutationpreflight.repository import (
    EvidenceFiles,
    FileDocuments,
    SandboxMaterializer,
)
from workflow_scripts.mutationpreflight.resolution import resolve
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


def refusing_resolver(name: str, cwd: Path, environment: typ.Mapping[str, str]) -> str:
    """Fail the test: a query phase must not look a command up at all."""
    message = f"a query phase must not resolve {name!r} in {cwd!r} with {environment!r}"
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
        reader=FileDocuments(),
        sandbox=SandboxMaterializer(),
        evidence=EvidenceFiles(),
        resolver=refusing_resolver,
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
        assert (
            run_sandbox_phase(
                real.reader, real.root, real.mutmut_version, declaration
            ).status
            != "fail"
        )
        # A check legitimately runs a command, so the real comparison uses the
        # production runner and resolver rather than the query-phase ones that
        # refuse.
        executable = dc.replace(real, runner=running.run_command, resolver=resolve)
        assert run_checks_phase(executable, declaration).status == "pass"

        refusing = dc.replace(
            real,
            reader=RefusingRepository(),
            sandbox=RefusingRepository(),
            evidence=RefusingRepository(),
        )
        results = [
            run_interpreter_phase(refusing),
            judge_native(refusing, declaration),
            run_sandbox_phase(
                refusing.reader, refusing.root, refusing.mutmut_version, declaration
            ),
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


class RefusingResolver:
    """A command resolver that refuses without reading the host's PATH."""

    #: The reason every lookup gives.
    reason: typ.Final[str] = "the resolver stand-in refuses to look the command up"

    def __call__(
        self, name: str, cwd: Path, environment: typ.Mapping[str, str]
    ) -> tuple[str | None, str]:
        """Refuse to resolve *name*, naming what was asked."""
        return None, f"{self.reason} ({name!r} in {cwd!r})"


class TestCommandBoundary:
    """The commands phase resolves and runs only through its injected seams."""

    def test_a_probe_free_commands_phase_asks_only_the_resolver(
        self, tmp_path: Path
    ) -> None:
        """Without a version probe the phase must not touch PATH or the host."""
        root = repository(tmp_path / "repo", Checkout())
        declaration = Declaration(
            source=root / "pyproject.toml",
            lane="cargo",
            commands=(CommandRequirement(name="preflight-declared-tool"),),
        )
        run = dc.replace(
            _run(root, tmp_path / "out"),
            reader=RefusingRepository(),
            sandbox=RefusingRepository(),
            evidence=RefusingRepository(),
            resolver=RefusingResolver(),
        )

        result = run_commands_phase(run, declaration, probe_versions=False)

        assert result.status == "fail"
        reported = " ".join((*result.findings, str(result.detail)))
        assert RefusingResolver.reason in reported, (
            f"the finding must name the resolver's refusal: {result}"
        )


class TestOneResolutionSeam:
    """Every command path resolves through the resolver the run carries."""

    def test_a_refusing_resolver_stops_a_check_and_a_probe(
        self, tmp_path: Path
    ) -> None:
        """A check and a probe resolve through ``Run.resolver``, not a local import."""
        root = repository(tmp_path / "repo", Checkout())
        declaration = Declaration(
            source=root / "pyproject.toml",
            lane="cargo",
            native=NativeRequirement(
                package="tei-rapporteur",
                probe=("preflight-declared-tool", "--build"),
            ),
            checks=(
                Check(
                    name="collect",
                    command=("preflight-declared-tool",),
                    timeout_seconds=MAX_CHECK_TIMEOUT_SECONDS,
                ),
            ),
        )
        run = dc.replace(
            _run(root, tmp_path / "out"),
            reader=FileDocuments(),
            sandbox=SandboxMaterializer(),
            evidence=EvidenceFiles(),
            resolver=RefusingResolver(),
            runner=running.run_command,
        )

        checked = run_checks_phase(run, declaration)
        report = judge_native(run, declaration)
        probed = probe_native(run, declaration, report)

        assert checked.status == "fail"
        assert any(
            RefusingResolver.reason in finding for finding in checked.findings
        ), f"the check must report the resolver's refusal: {checked.findings}"
        assert probed.blocking, (
            "a probe whose command cannot resolve must not report success"
        )
        assert any(RefusingResolver.reason in finding for finding in probed.blocking), (
            f"the probe must report the resolver's refusal: {probed.blocking}"
        )


class TestResolverContract:
    """The resolver returns the executable, or the reason it found none."""

    def test_a_resolved_command_returns_its_executable_and_no_reason(
        self, tmp_path: Path
    ) -> None:
        """A command that resolves yields a path, with an empty explanation."""
        executable, problem = resolve(sys.executable, tmp_path, dict(os.environ))

        assert executable == sys.executable
        assert problem == ""

    def test_an_unresolved_command_returns_no_executable_and_a_reason(
        self, tmp_path: Path
    ) -> None:
        """A command that does not resolve yields ``None`` and why."""
        executable, problem = resolve(
            "a-command-this-test-invented", tmp_path, {"PATH": str(tmp_path)}
        )

        assert executable is None
        assert problem, "a command that does not resolve must say why"


class TestSandboxOwnership:
    """The run removes a sandbox it built, and never the checkout it read."""

    @staticmethod
    def _recording_run(root: Path) -> tuple[Run, list[Path]]:
        """Return a run over *root* whose sandbox records every discard."""
        discarded: list[Path] = []

        class RecordingSandbox:
            """Records what the run asks to remove, then delegates."""

            def materialize(
                self, checkout: Path, contract: CopyContract
            ) -> tuple[Path | None, tuple[str, ...]]:
                """Build the sandbox the production adapter would build."""
                return SandboxMaterializer().materialize(checkout, contract)

            def discard(self, sandbox: Path) -> None:
                """Record the sandbox the run asks to remove."""
                discarded.append(sandbox)

        # The checks legitimately run commands, so this run uses the production
        # runner and resolver; only the sandbox is recorded.
        run = dc.replace(
            _run(root, root.parent / "out"),
            sandbox=RecordingSandbox(),
            runner=running.run_command,
            resolver=resolve,
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
        declaration = Declaration(
            source=root / "pyproject.toml",
            lane="cargo",
            checks=(
                Check(
                    name="collect",
                    command=(sys.executable, "-c", "print(1)"),
                    timeout_seconds=MAX_CHECK_TIMEOUT_SECONDS,
                ),
            ),
        )

        result = run_checks_phase(run, declaration)

        assert result.status == "pass", result
        assert discarded == [], "a checkout the run only read must never be removed"
