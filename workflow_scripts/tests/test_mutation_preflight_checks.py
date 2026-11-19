"""Declared checks: their boundary, their logs and their sandbox.

Each case is one of the failures issue #591 recorded: a check that passes in
the checkout but not where the mutants are built, a command resolved against a
directory other than the one it runs in, and evidence that must stay bounded
and stay apart from its neighbour's.
"""

from __future__ import annotations

import stat
import sys
import typing as typ
from pathlib import Path

from workflow_scripts.mutationpreflight.constants import LOG_BOUND_BYTES
from workflow_scripts.mutationpreflight.running import (
    TRUNCATION_PREFIX,
    resolution_problem,
)
from workflow_scripts.tests.mutation_preflight_support import (
    POSIX_SHIMS_ONLY,
    POSIX_SYMLINKS_ONLY,
    Checkout,
    Outcome,
    outcome_for,
    repository,
)


class TestChecks:
    """Declared checks run from the boundary they declare."""

    def test_a_check_runs_from_its_declared_cwd_and_environment(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """cwd, import root and environment are the ones the declaration names."""
        script = (
            "import os, pathlib; "
            'print(pathlib.Path.cwd().name, os.environ.get("MARKER"), '
            'os.environ.get("PYTHONPATH"))'
        )
        manifest = (
            "[[checks]]\n"
            'name = "collect"\n'
            f"command = ['{sys.executable}', '-c', '{script}']\n"
            'cwd = "workflow_scripts/tests"\n'
            "timeout_seconds = 30\n"
            'env = { MARKER = "declared", PYTHONPATH = ".." }\n'
        )

        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            Checkout(manifest=manifest),
            project_python="3.13",
        )

        assert outcome.exit_code == 0, outcome.stdout
        printed = outcome.evidence("checks", "collect")
        assert printed["cwd"] == "workflow_scripts/tests"
        assert str(printed["stdout"]).split() == ["tests", "declared", ".."], (
            "the declared cwd, environment and import root must reach the command"
        )

    def test_a_failing_check_names_the_check_and_its_last_line(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A collection failure is reported as itself, not as a mutant failure."""
        script = 'import sys; sys.stderr.write("boom: no such option"); sys.exit(2)'
        manifest = (
            "[[checks]]\n"
            'name = "collect"\n'
            f"command = ['{sys.executable}', '-c', '{script}']\n"
        )

        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            Checkout(manifest=manifest),
            project_python="3.13",
        )

        assert outcome.exit_code == 1, (
            "a check that exits non-zero must fail the run, not be reported as passing"
        )
        found = outcome.findings("checks")
        assert "check 'collect' failed with exit code 2" in found, (
            "the finding must name the check and the status it exited with"
        )
        assert "boom: no such option" in found, (
            "the finding must quote the last line the check printed, so the "
            "failure is readable without the log"
        )

    def test_a_hung_check_is_bounded_by_its_timeout(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A check that never returns is a named failure, not a stuck job."""
        script = "import time; time.sleep(30)"
        manifest = (
            "[[checks]]\n"
            'name = "slow"\n'
            f"command = ['{sys.executable}', '-c', '{script}']\n"
            "timeout_seconds = 1\n"
        )

        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            Checkout(manifest=manifest),
            project_python="3.13",
        )

        assert outcome.exit_code == 1, (
            "a check that outlives its timeout must fail the run, not hang it"
        )
        finding = outcome.findings("checks")
        assert "did not finish within 1 second" in finding, (
            "the finding must name the ceiling the declaration set"
        )
        assert "1 seconds" not in finding, (
            "the unit must agree with the ceiling, so the message reads as prose"
        )


class TestCheckLogs:
    """Each check leaves bounded evidence, kept apart from its neighbours."""

    def test_a_check_keeps_bounded_logs(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """Even a passing check leaves its streams for the job's diagnostics."""
        script = 'print("collected 12 tests")'
        manifest = (
            "[[checks]]\n"
            'name = "collect"\n'
            f"command = ['{sys.executable}', '-c', '{script}']\n"
        )

        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            Checkout(manifest=manifest),
            project_python="3.13",
        )

        assert outcome.exit_code == 0, outcome.stdout
        log = tmp_path / "out" / "checks" / "00-collect.stdout.log"
        assert "collected 12 tests" in log.read_text(encoding="utf-8")

    def test_a_flooding_check_is_truncated_while_it_is_read(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A command that never stops printing is bounded as it is read."""
        script = f'import sys; sys.stdout.write("x" * {4 * LOG_BOUND_BYTES})'
        manifest = (
            "[[checks]]\n"
            'name = "collect"\n'
            f"command = ['{sys.executable}', '-c', '{script}']\n"
        )

        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            Checkout(manifest=manifest),
            project_python="3.13",
        )

        assert outcome.exit_code == 0, outcome.stdout
        written = (tmp_path / "out" / "checks" / "00-collect.stdout.log").read_text(
            encoding="utf-8"
        )
        assert written == (
            f"{'x' * LOG_BOUND_BYTES}\n"
            f"{TRUNCATION_PREFIX}{3 * LOG_BOUND_BYTES} bytes truncated]"
        ), (
            "the log must hold exactly the first bound's worth of bytes and "
            "report the exact number it dropped"
        )

    def test_a_long_stream_is_retained_in_order(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """Retained bytes are a prefix the check actually wrote, in order."""
        script = 'import sys; [print("line-%04d" % index) for index in range(2000)]'
        manifest = (
            "[[checks]]\n"
            'name = "collect"\n'
            f"command = ['{sys.executable}', '-c', '{script}']\n"
        )

        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            Checkout(manifest=manifest),
            project_python="3.13",
        )

        assert outcome.exit_code == 0, outcome.stdout
        log = tmp_path / "out" / "checks" / "00-collect.stdout.log"
        retained_bytes, separator, _ = log.read_bytes().partition(b"[... ")
        assert separator, "a flooding check must be truncated, not kept whole"
        assert len(retained_bytes) >= LOG_BOUND_BYTES, (
            "the bound must keep every byte it can hold"
        )
        # The bound cuts on a byte count, not a line boundary, and Windows
        # writes two bytes per newline, so the retained prefix may end
        # part-way through a line. It must still be the stream's own opening.
        expected = "".join(f"line-{index:04d}\n" for index in range(2000))
        retained = (
            retained_bytes.decode("utf-8", "ignore")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
            .rstrip("\n")
        )
        assert expected.startswith(retained), (
            "the retained prefix must be the bytes the check wrote, in order, "
            "so a reader can trust what sits before the marker"
        )

    def test_checks_whose_names_sanitize_alike_keep_separate_logs(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A later check must not overwrite an earlier check's evidence."""
        manifest = (
            "[[checks]]\n"
            'name = "a/b"\n'
            f"command = ['{sys.executable}', '-c', 'print(\"first\")']\n"
            "[[checks]]\n"
            'name = "a-b"\n'
            f"command = ['{sys.executable}', '-c', 'print(\"second\")']\n"
        )

        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            Checkout(manifest=manifest),
            project_python="3.13",
        )

        assert outcome.exit_code == 0, outcome.stdout
        logs = tmp_path / "out" / "checks"
        first = (logs / "00-a-b.stdout.log").read_text(encoding="utf-8")
        second = (logs / "01-a-b.stdout.log").read_text(encoding="utf-8")
        assert first.strip() == "first", (
            "the first check's log must hold the first check's output"
        )
        assert second.strip() == "second", (
            "a name sanitized onto the same stem must not overwrite the first log"
        )
        results = typ.cast(
            "list[dict[str, object]]", outcome.detail("checks")["results"]
        )
        published = [(item["index"], item["name"]) for item in results]
        assert published == [(0, "a/b"), (1, "a-b")], (
            "each check's evidence must stay under its own index and name"
        )


def test_an_unwritable_log_fails_the_check(
    tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
) -> None:
    """Evidence nobody can read must not be reported as a passing check."""
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "checks").write_text("", encoding="utf-8")
    manifest = (
        "[[checks]]\n"
        'name = "collect"\n'
        f"command = ['{sys.executable}', '-c', 'print(1)']\n"
    )

    outcome = outcome_for(
        run_preflight,
        tmp_path / "repo",
        Checkout(manifest=manifest),
        project_python="3.13",
    )

    assert outcome.exit_code == 1, (
        "a check whose evidence cannot be kept has not passed"
    )
    assert "could not be written" in outcome.findings("checks"), (
        "the finding must name the log that could not be written"
    )


class TestCommandResolution:
    """A path in a command is resolved against the directory it runs in."""

    @POSIX_SHIMS_ONLY
    def test_a_relative_command_path_is_resolved_against_its_cwd(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A path command must resolve where it runs, not where the helper started."""
        manifest = (
            '[[checks]]\nname = "collect"\ncommand = ["./probe"]\ncwd = "fixtures"\n'
        )
        root = repository(
            tmp_path / "repo",
            Checkout(manifest=manifest, also_copy=("conftest.py", "fixtures")),
        )
        probe = root / "fixtures" / "probe"
        probe.parent.mkdir(parents=True)
        probe.write_text("#!/bin/sh\necho collected\n", encoding="utf-8")
        probe.chmod(probe.stat().st_mode | stat.S_IXUSR)

        outcome = run_preflight(root, project_python="3.13")

        assert outcome.exit_code == 0, outcome.stdout
        assert outcome.phases["checks"]["status"] == "pass", outcome.stdout

    @POSIX_SYMLINKS_ONLY
    def test_a_relative_symlinked_command_runs_through_its_link(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A declared link is the path that runs, so a venv keeps its packages."""
        manifest = (
            '[[checks]]\nname = "collect"\ncommand = ["./probe"]\ncwd = "fixtures"\n'
        )
        root = repository(
            tmp_path / "repo",
            Checkout(manifest=manifest, also_copy=("conftest.py", "fixtures")),
        )
        fixtures = root / "fixtures"
        fixtures.mkdir(parents=True)
        target = fixtures / "real-probe"
        target.write_text('#!/bin/sh\necho "$0"\n', encoding="utf-8")
        target.chmod(target.stat().st_mode | stat.S_IXUSR)
        (fixtures / "probe").symlink_to(target)

        outcome = run_preflight(root, project_python="3.13")

        assert outcome.exit_code == 0, outcome.stdout
        printed = outcome.evidence("checks", "collect")
        assert str(printed["stdout"]).strip().endswith("fixtures/probe"), (
            "the declared link must be the executable path, so an interpreter "
            "reached through it still finds the environment that named it"
        )

    @POSIX_SYMLINKS_ONLY
    def test_a_parent_component_after_a_link_resolves_through_the_link(
        self, tmp_path: Path
    ) -> None:
        """'..' after a symlink is the target's parent, as the OS resolves it."""
        fixtures = tmp_path / "fixtures"
        (fixtures / "sub" / "deep").mkdir(parents=True)
        probe = fixtures / "sub" / "probe"
        probe.write_text("", encoding="utf-8")
        probe.chmod(probe.stat().st_mode | stat.S_IXUSR)
        (fixtures / "link").symlink_to(Path("sub") / "deep")

        problem = resolution_problem("./link/../probe", fixtures)

        assert problem == "", (
            "collapsing '..' lexically would name fixtures/probe and miss "
            f"fixtures/sub/probe, where the operating system looks: {problem}"
        )

    @POSIX_SHIMS_ONLY
    def test_an_explicit_path_must_name_an_executable_file(
        self, tmp_path: Path
    ) -> None:
        """A declared path is checked, not assumed, so a plain file is refused."""
        tools = tmp_path / "repo" / "tools"
        tools.mkdir(parents=True)
        probe = tools / "probe"
        probe.write_text("#!/bin/sh\necho collected\n", encoding="utf-8")
        probe.chmod(probe.stat().st_mode & ~stat.S_IXUSR)

        problem = resolution_problem("./tools/probe", tools)

        assert "is not an executable file" in problem, (
            f"a file the operating system may not execute must not resolve: {problem}"
        )

    @POSIX_SYMLINKS_ONLY
    def test_a_cwd_resolving_outside_the_checkout_is_refused(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A symlinked cwd would run the check where the checkout does not reach."""
        outside = tmp_path / "outside"
        outside.mkdir()
        manifest = (
            "[[checks]]\n"
            'name = "collect"\n'
            f"command = ['{sys.executable}', '-c', 'print(1)']\n"
            'cwd = "linked"\n'
        )
        root = repository(
            tmp_path / "repo",
            Checkout(manifest=manifest, also_copy=("conftest.py", "linked")),
        )
        (root / "linked").symlink_to(outside, target_is_directory=True)

        outcome = run_preflight(root, project_python="3.13")

        assert outcome.exit_code == 1, outcome.stdout
        assert "outside the sandbox" in outcome.findings("checks"), (
            "the finding must name the cwd that left the sandbox"
        )


class TestCheckSandbox:
    """A check runs in the copy the mutation run will actually receive."""

    @staticmethod
    def _check(script: str, prefix: str = "") -> str:
        """Return a declaration with one check running *script*.

        *script* is placed in a TOML literal string, so it must not contain a
        single quote or a raw newline.
        """
        return (
            f"{prefix}"
            "[[checks]]\n"
            'name = "collect"\n'
            f"command = ['{sys.executable}', '-c', '{script}']\n"
        )

    def test_a_check_that_the_checkout_satisfies_fails_in_the_sandbox(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A helper the copy set omits is absent where the mutation run will be."""
        root = repository(
            tmp_path / "repo",
            Checkout(
                manifest=self._check("import helper; print(helper.VALUE)"),
                extra={"helper.py": "VALUE = 1\n"},
            ),
        )

        outcome = run_preflight(root, project_python="3.13")

        assert outcome.exit_code == 1, outcome.stdout
        assert "check 'collect' failed" in outcome.findings("checks"), (
            "a check that only the checkout satisfies must fail in the sandbox"
        )
        assert "No module named 'helper'" in outcome.findings("checks"), (
            "the finding must come from the check's own import failure"
        )
        ran_in = Path(str(outcome.detail("checks")["ran_in"]))
        assert ran_in != root, "the check ran in the checkout, not the sandbox"
        assert not ran_in.exists(), "the sandbox must not outlive the run"

    def test_naming_the_helper_in_also_copy_repairs_the_sandbox_check(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """The same check passes once the sandbox is told to copy the helper."""
        root = repository(
            tmp_path / "repo",
            Checkout(
                manifest=self._check("import helper; print(helper.VALUE)"),
                also_copy=("conftest.py", "helper.py"),
                extra={"helper.py": "VALUE = 1\n"},
            ),
        )

        outcome = run_preflight(root, project_python="3.13")

        assert outcome.exit_code == 0, outcome.stdout
        collected = outcome.evidence("checks", "collect")
        assert str(collected["stdout"]).strip() == "1", (
            "the copied helper must be importable where the check runs"
        )
        assert Path(str(outcome.detail("checks")["ran_in"])) != root, (
            "the passing check must have run in the sandbox, not the checkout"
        )

    def test_the_sandbox_follows_the_selected_release_copy_contract(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A check observes the copy set the selected release actually delivers."""
        manifest = self._check('import pathlib; pathlib.Path("uv.lock").read_text()')
        root = repository(
            tmp_path / "repo",
            Checkout(manifest=manifest, extra={"uv.lock": ""}),
        )

        older = run_preflight(root, project_python="3.13", mutmut_version="3.6.0")
        newer = run_preflight(root, project_python="3.13", mutmut_version="3.7.0")

        assert older.exit_code == 1, (
            "mutmut 3.6's copy set omits uv.lock, so a check that reads it must "
            "fail where the mutants are built rather than in the checkout"
        )
        assert "FileNotFoundError" in older.findings("checks"), (
            "the failure must come from the check reading the absent file, not "
            "from the sandbox classifier that reported it"
        )
        assert newer.exit_code == 0, newer.stdout
        assert newer.phases["checks"]["status"] == "pass", (
            "mutmut 3.7's copy set delivers uv.lock, so the same check must pass"
        )

    def test_an_unknown_release_leaves_the_copy_contract_unknown(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """An unrecognized release is reported, never judged by another's set."""
        root = repository(
            tmp_path / "repo",
            Checkout(
                manifest=self._check("print(1)", 'sandbox_paths = ["README.md"]\n'),
                extra={"README.md": "# example\n"},
            ),
        )

        outcome = run_preflight(root, project_python="3.13", mutmut_version="9.9.9")

        assert outcome.exit_code == 0, outcome.stdout
        assert outcome.phases["sandbox"]["status"] == "unknown", (
            "an unrecognized release must leave the sandbox unjudged"
        )
        assert outcome.phases["checks"]["status"] == "unknown", (
            "a check that was never run has not passed, so its phase is unknown"
        )
        assert "the copy contract for mutmut 9.9.9 is not established" in (
            outcome.findings("checks")
        ), "the finding must name the release whose contract is missing"
