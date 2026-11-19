"""The preflight's boundaries: queries, sandbox copies, and cleanup.

A phase that answers a question must leave nothing behind, the sandbox must
copy a link the way mutmut's own ``copytree`` does, and a descendant that
outlives its command must not stall the report.
"""

from __future__ import annotations

import dataclasses as dc
import os
import signal
import sys
import time
import typing as typ

import pytest

from workflow_scripts.mutationpreflight import running
from workflow_scripts.mutationpreflight.constants import MAX_CHECK_TIMEOUT_SECONDS
from workflow_scripts.mutationpreflight.copy_set import CopyContract
from workflow_scripts.mutationpreflight.declaration import load_declaration
from workflow_scripts.mutationpreflight.interpreters import run_interpreter_phase
from workflow_scripts.mutationpreflight.materialize import discard, materialize
from workflow_scripts.mutationpreflight.models import Report, Runtimes
from workflow_scripts.mutationpreflight.native import run_native_phase
from workflow_scripts.mutationpreflight.ports import Invocation, Run
from workflow_scripts.mutationpreflight.reporting import checked_phases, write_report
from workflow_scripts.mutationpreflight.sandbox import run_sandbox_phase
from workflow_scripts.tests.mutation_preflight_support import (
    POSIX_SYMLINKS_ONLY,
    Checkout,
    Outcome,
    lock_document,
    repository,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

#: A descendant holding a pipe is only reachable through a POSIX process
#: group and ``setsid``, so the case runs where those exist.
POSIX_PROCESSES_ONLY = pytest.mark.skipif(
    sys.platform == "win32", reason="needs a POSIX process group"
)


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
        runner=_refuse,
    )


class TestQueryPhases:
    """A phase that answers a question writes nothing and runs nothing."""

    def test_the_query_phases_leave_no_evidence_behind(self, tmp_path: Path) -> None:
        """Reading a declaration must not create a file or a directory."""
        root = repository(
            tmp_path / "repo",
            Checkout(
                manifest=(
                    'sandbox_paths = ["README.md"]\n'
                    '[native]\npackage = "tei-rapporteur"\n'
                ),
                also_copy=("conftest.py", "README.md"),
                extra={
                    "README.md": "# example\n",
                    "uv.lock": lock_document("0.3.1", 'registry = "x"'),
                },
            ),
        )
        output = tmp_path / "out"
        run = _run(root, output)
        loaded = load_declaration(root, "mutmut")
        assert loaded.declaration is not None, "the fixture declares requirements"
        before = sorted(tmp_path.rglob("*"))

        results = [
            run_interpreter_phase(run),
            run_native_phase(run, loaded.declaration),
            run_sandbox_phase(run.root, loaded.declaration, run.mutmut_version),
        ]

        after = sorted(tmp_path.rglob("*"))
        assert after == before, "a query phase must not write or create anything"
        assert not output.exists(), "a query phase must not create its output directory"
        names = [result.name for result in results]
        assert names == ["interpreter", "native", "sandbox"], names

    def test_write_report_is_the_only_writer(self, tmp_path: Path) -> None:
        """The command line's single write is the one that publishes the run."""
        root = repository(tmp_path / "repo", Checkout())
        output = tmp_path / "out"
        run = _run(root, output)
        report = Report(
            lane="mutmut", runtimes=run.runtimes, results=(), verdict="pass"
        )

        problems = write_report(run, report)

        assert problems == (), "publishing a readable report must not report a problem"
        written = output / "mutation-preflight.json"
        assert written.is_file(), "the report must be the artefact a later stage reads"
        assert not (output / "checks").exists(), (
            "a run with no checks must not create a checks directory"
        )


class TestStreaming:
    """Each phase is published as it finishes, not held to the end."""

    def test_the_manifest_envelope_precedes_the_interpreter_phase(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The envelope is a stream, so a reader learns the manifest first."""
        root = repository(tmp_path / "repo", Checkout(requires_python=">=3.12"))
        seen: list[str] = []

        def rule(requirement: object, interpreter: str, *, subject: str) -> list[str]:
            """Record what was printed so far, and report no violation."""
            seen.append(capsys.readouterr().out)
            return []

        run = dc.replace(_run(root, tmp_path / "out"), rule=rule)
        checked_phases(run, load_declaration(root, "mutmut"))

        assert seen, "the interpreter phase must have judged the project's range"
        assert "mutation_preflight_phase=" in seen[0], (
            "the manifest envelope must already have been printed"
        )
        assert '"name": "manifest"' in seen[0], (
            "the manifest envelope must precede the interpreter phase's work"
        )


class TestDeclarationRules:
    """A declaration cannot widen a limit the helper owns."""

    def test_a_declared_timeout_is_capped_at_the_maximum(self, tmp_path: Path) -> None:
        """A declaration asking for more than the maximum gets the maximum."""
        root = repository(
            tmp_path / "repo",
            Checkout(
                manifest=(
                    "[[checks]]\n"
                    'name = "collect"\n'
                    'command = ["true"]\n'
                    "timeout_seconds = 5000\n"
                )
            ),
        )

        loaded = load_declaration(root, "mutmut")

        assert loaded.declaration is not None, "the declaration must parse"
        effective = loaded.declaration.checks[0].timeout_seconds
        assert effective == MAX_CHECK_TIMEOUT_SECONDS, (
            "a declared ceiling above the maximum must be capped, not honoured"
        )


class TestMaterialize:
    """The sandbox mirrors mutmut's copytree, including its link handling."""

    @POSIX_SYMLINKS_ONLY
    def test_an_internal_symlink_is_materialized_by_content(
        self, tmp_path: Path
    ) -> None:
        """A link inside the checkout is copied as the tree it points at."""
        root = tmp_path / "repo"
        (root / "real").mkdir(parents=True)
        (root / "real" / "widget.py").write_text("VALUE = 1\n", encoding="utf-8")
        (root / "linked").symlink_to(root / "real", target_is_directory=True)

        sandbox, problems = materialize(root, CopyContract(("linked",), ()))

        assert problems == (), "copying a link inside the checkout must succeed"
        assert sandbox is not None
        try:
            copied = sandbox / "linked" / "widget.py"
            assert copied.read_text(encoding="utf-8") == "VALUE = 1\n", (
                "the sandbox must hold the content the link points at"
            )
            assert not (sandbox / "linked").is_symlink(), (
                "mutmut follows an internal link instead of copying the link"
            )
        finally:
            discard(sandbox)

    @POSIX_SYMLINKS_ONLY
    def test_a_link_leaving_the_checkout_stays_a_link(self, tmp_path: Path) -> None:
        """A link out of the checkout is preserved, not silently imported."""
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("x", encoding="utf-8")
        root = tmp_path / "repo"
        root.mkdir()
        (root / "escape").symlink_to(outside, target_is_directory=True)

        sandbox, problems = materialize(root, CopyContract(("escape",), ()))

        assert problems == ()
        assert sandbox is not None
        try:
            link = sandbox / "escape"
            assert link.is_symlink(), (
                "a link out of the checkout must be kept, so the sandbox does not "
                "silently contain a tree the declaration never named"
            )
            assert link.readlink() == outside, (
                "the preserved link must name the target the declaration saw"
            )
        finally:
            discard(sandbox)


class TestCleanup:
    """A command's cleanup is bounded even when a descendant holds its pipe."""

    @POSIX_PROCESSES_ONLY
    def test_a_descendant_holding_the_pipe_does_not_block_cleanup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A writer the preflight cannot reach must not stall its own report."""
        monkeypatch.setattr(running, "_DRAIN_GRACE_SECONDS", 0.1)
        pid_file = tmp_path / "child.pid"
        script = (
            "import pathlib, subprocess, sys, time\n"
            "child = subprocess.Popen(\n"
            "    [sys.executable, '-c', 'import time; time.sleep(30)'],\n"
            "    start_new_session=True, stdout=sys.stdout,\n"
            ")\n"
            f"pathlib.Path({str(pid_file)!r}).write_text(str(child.pid))\n"
            "print('started', flush=True)\n"
            "time.sleep(30)\n"
        )

        started = time.monotonic()
        outcome, problem = running.run_command(
            Invocation(
                (sys.executable, "-c", script),
                cwd=tmp_path,
                env={},
                timeout=1,
                ambient=dict(os.environ),
            )
        )
        elapsed = time.monotonic() - started

        assert pid_file.is_file(), "the descendant must start before the timeout"
        try:
            assert outcome is not None, "a timeout must still return what was printed"
            assert outcome.timed_out is True
            assert problem == "it did not finish within 1 second"
            assert "started" in outcome.stdout, (
                "a timed-out command keeps what it printed before it was stopped"
            )
            assert elapsed < 5, (
                "a pipe a descendant still holds must not stall the report"
            )
        finally:
            # The descendant made its own session, so the process-group kill
            # could not reach it. The test reaps what it started.
            os.kill(int(pid_file.read_text(encoding="utf-8")), signal.SIGKILL)


class TestUsageErrors:
    """A checkout the phases cannot read is a usage error, not a finding."""

    def test_a_repository_that_is_not_a_directory_is_refused(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A mistyped path must not be reported as a missing declaration."""
        not_a_checkout = tmp_path / "checkout"
        not_a_checkout.write_text("", encoding="utf-8")

        outcome = run_preflight(not_a_checkout, project_python="3.13")

        assert outcome.exit_code == 2, (
            "a path the phases cannot read is a usage error, not a phase failure"
        )
        assert outcome.result == "fail"
        assert "is not a directory" in outcome.stderr
