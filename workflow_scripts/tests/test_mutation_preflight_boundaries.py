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
from workflow_scripts.mutationpreflight.native import judge_native
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

#: The most cleanup may take at the production grace: the one-second timeout
#: plus one shared grace period, with room for the poll and process teardown.
#: A grace waited per stream would take about eleven seconds and fail it.
_PRODUCTION_CLEANUP_CEILING: typ.Final[float] = 8.5


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
            judge_native(run, loaded.declaration),
            run_sandbox_phase(run.root, loaded.declaration, run.mutmut_version),
        ]

        after = sorted(tmp_path.rglob("*"))
        assert after == before, "a query phase must not write or create anything"
        assert not output.exists(), "a query phase must not create its output directory"
        names = [result.name for result in results]
        assert names == ["interpreter", "native", "sandbox"], names

    def test_a_declared_probe_is_not_run_by_the_native_judgement(
        self, tmp_path: Path
    ) -> None:
        """Judging the lock and range is a query; the probe is composed onto it."""
        root = repository(
            tmp_path / "repo",
            Checkout(
                manifest=(
                    "[native]\npackage = \"tei-rapporteur\"\nprobe = ['false']\n"
                ),
                extra={"uv.lock": lock_document("0.3.1", 'registry = "x"')},
            ),
        )
        run = _run(root, tmp_path / "out")
        loaded = load_declaration(root, "mutmut")
        assert loaded.declaration is not None, "the fixture declares requirements"

        result = judge_native(run, loaded.declaration)

        assert result.status in {"unknown", "skipped"}, (
            "the judgement must not claim a probe it never ran: "
            f"{result.status} {result.findings}"
        )
        assert "probe" not in result.detail, (
            "the probe's evidence belongs to the composition, not the judgement"
        )

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

    def test_write_report_owns_the_summary_and_the_report(self, tmp_path: Path) -> None:
        """A summary path is written by the publisher, never by a query phase."""
        root = repository(
            tmp_path / "repo",
            Checkout(
                manifest='sandbox_paths = ["README.md"]\n'
                '[native]\npackage = "tei-rapporteur"\n',
                also_copy=("conftest.py", "README.md"),
                extra={
                    "README.md": "# example\n",
                    "uv.lock": lock_document("0.3.1", 'registry = "x"'),
                },
            ),
        )
        output = tmp_path / "out"
        summary = tmp_path / "summary.md"
        summary.write_text("", encoding="utf-8")
        run = dc.replace(_run(root, output), summary_path=str(summary))
        loaded = load_declaration(root, "mutmut")
        assert loaded.declaration is not None, "the fixture declares requirements"
        report = Report(
            lane="mutmut", runtimes=run.runtimes, results=(), verdict="pass"
        )

        run_interpreter_phase(run)
        judge_native(run, loaded.declaration)
        run_sandbox_phase(run.root, loaded.declaration, run.mutmut_version)

        assert summary.read_text(encoding="utf-8") == "", (
            "a query phase must not append to the job summary"
        )
        assert not output.exists(), "a query phase must not write the report"

        problems = write_report(run, report)

        assert problems == (), "publishing a readable report must not report a problem"
        assert "Mutation environment preflight" in summary.read_text(
            encoding="utf-8"
        ), "the job summary must be written by write_report alone"
        assert (output / "mutation-preflight.json").is_file(), (
            "the report must be written by write_report alone"
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

        assert problems == (), (
            f"copying a link inside the checkout must succeed: {problems}"
        )
        assert sandbox is not None, (
            f"an internal link must be materialized, not refused: {problems}"
        )
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

        assert problems == (), (
            f"keeping an escaping link must not itself fail the copy: {problems}"
        )
        assert sandbox is not None, (
            f"the sandbox must still be built for an escaping link: {problems}"
        )
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

    @POSIX_SYMLINKS_ONLY
    def test_a_link_nested_in_a_copied_directory_stays_a_link(
        self, tmp_path: Path
    ) -> None:
        """The containment rule holds at every level of a copied directory."""
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("x", encoding="utf-8")
        root = tmp_path / "repo"
        (root / "pkg").mkdir(parents=True)
        (root / "pkg" / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
        (root / "pkg" / "escape").symlink_to(outside, target_is_directory=True)

        sandbox, problems = materialize(root, CopyContract(("pkg",), ()))

        assert problems == (), (
            f"copying a directory with a link must succeed: {problems}"
        )
        assert sandbox is not None, f"the sandbox must be built: {problems}"
        try:
            copied = sandbox / "pkg" / "module.py"
            assert copied.read_text(encoding="utf-8") == "VALUE = 1\n", (
                "the directory's own files must still be copied"
            )
            link = sandbox / "pkg" / "escape"
            assert link.is_symlink(), (
                "a link deeper in a copied tree must keep the containment rule"
            )
            assert link.readlink() == outside, (
                "the nested link must name the target the checkout saw"
            )
        finally:
            discard(sandbox)

    @POSIX_SYMLINKS_ONLY
    def test_a_path_beyond_an_escaping_link_keeps_the_link(
        self, tmp_path: Path
    ) -> None:
        """A declared path behind an escaping link must not copy its target."""
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("x", encoding="utf-8")
        root = tmp_path / "repo"
        (root / "tests").mkdir(parents=True)
        (root / "tests" / "escape").symlink_to(outside, target_is_directory=True)

        sandbox, problems = materialize(
            root, CopyContract(("tests/escape/secret.txt",), ())
        )

        assert problems == (), (
            f"keeping the escaping ancestor must not fail the copy: {problems}"
        )
        assert sandbox is not None, f"the sandbox must be built: {problems}"
        try:
            link = sandbox / "tests" / "escape"
            assert link.is_symlink(), (
                "the link the declared path is reached through must be kept, so "
                "the sandbox does not receive the external tree"
            )
            assert link.readlink() == outside, (
                "the kept link must name the target the checkout saw"
            )
            assert sorted(entry.name for entry in (sandbox / "tests").iterdir()) == [
                "escape"
            ], "the declared file must not be copied into the sandbox as content"
            assert (link / "secret.txt").is_file(), (
                "the declared path must still resolve through the kept link"
            )
        finally:
            discard(sandbox)

    @POSIX_SYMLINKS_ONLY
    def test_overlapping_entries_keep_a_link_without_failing_the_copy(
        self, tmp_path: Path
    ) -> None:
        """The default tests entry and an also_copy naming a link both copy it."""
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("x", encoding="utf-8")
        root = tmp_path / "repo"
        (root / "tests").mkdir(parents=True)
        (root / "tests" / "test_module.py").write_text("", encoding="utf-8")
        (root / "tests" / "escape").symlink_to(outside, target_is_directory=True)

        sandbox, problems = materialize(
            root, CopyContract(("tests", "tests/escape"), ())
        )

        assert problems == (), (
            f"an overlapping entry must not fail the copy it overlaps: {problems}"
        )
        assert sandbox is not None, f"the sandbox must be built: {problems}"
        try:
            link = sandbox / "tests" / "escape"
            assert link.is_symlink(), (
                "the link the first entry kept must survive the second entry"
            )
            assert link.readlink() == outside, (
                "the kept link must name the target the checkout saw"
            )
            assert (sandbox / "tests" / "test_module.py").is_file(), (
                "the overlapping entry must not disturb the directory it sits in"
            )
        finally:
            discard(sandbox)

    @POSIX_SYMLINKS_ONLY
    def test_a_glob_match_that_leaves_the_checkout_stays_a_link(
        self, tmp_path: Path
    ) -> None:
        """A glob match receives the same containment rule as a declared entry."""
        outside = tmp_path / "outside.txt"
        outside.write_text("x", encoding="utf-8")
        root = tmp_path / "repo"
        root.mkdir()
        (root / "escape.txt").symlink_to(outside)

        sandbox, problems = materialize(root, CopyContract((), ("*.txt",)))

        assert problems == (), f"copying a glob match must succeed: {problems}"
        assert sandbox is not None, f"the sandbox must be built: {problems}"
        try:
            link = sandbox / "escape.txt"
            assert link.is_symlink(), (
                "a glob match that leaves the checkout must be kept as a link"
            )
            assert link.readlink() == outside, (
                "the preserved link must name the target the checkout saw"
            )
        finally:
            discard(sandbox)


class TestCleanup:
    """A command's cleanup is bounded even when a descendant holds its pipe."""

    @POSIX_PROCESSES_ONLY
    @pytest.mark.parametrize(
        ("grace", "ceiling"),
        [
            (0.1, 5.0),
            (running._DRAIN_GRACE_SECONDS, _PRODUCTION_CLEANUP_CEILING),
        ],
        ids=["short-grace", "production-grace"],
    )
    def test_a_descendant_holding_the_pipe_does_not_block_cleanup(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        grace: float,
        ceiling: float,
    ) -> None:
        """A writer the preflight cannot reach must not stall its own report."""
        monkeypatch.setattr(running, "_DRAIN_GRACE_SECONDS", grace)
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
            assert outcome.timed_out is True, (
                "a command stopped at its ceiling must be recorded as timed out"
            )
            assert problem == "it did not finish within 1 second", (
                f"the reason must name the ceiling that stopped it: {problem!r}"
            )
            assert "started" in outcome.stdout, (
                "a timed-out command keeps what it printed before it was stopped"
            )
            assert elapsed < ceiling, (
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
        assert outcome.result == "fail", (
            "a usage error must fail the run rather than pass it silently"
        )
        assert "is not a directory" in outcome.stderr, (
            f"the usage error must say what was wrong: {outcome.stderr!r}"
        )
