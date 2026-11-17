"""The mutation preflight names what a run would fail on, before it starts.

Each case here is one of the failures issue #591 recorded: a missing README, a
hidden workflow tree, a generator sibling, a Rust toolchain file, a nested
fixture, an interpreter outside the project's range, an absent mold or
mdtablefix. The assertion in each case is that the phase fails *naming the
path, command or boundary*, and the paired case shows the same repository
passing once the requirement is declared or satisfied.
"""

from __future__ import annotations

import dataclasses as dc
import json
import stat
import sys
import typing as typ

import pytest
from plumbum import local

from workflow_scripts import mutation_preflight as preflight

if typ.TYPE_CHECKING:
    from pathlib import Path

# The fake tool shims are POSIX shell scripts; the reusable workflows only run
# on ubuntu-latest, and on Windows the real tools would answer instead.
POSIX_SHIMS_ONLY = pytest.mark.skipif(
    sys.platform == "win32", reason="fake tool helpers emit POSIX sh"
)

MUTMUT_TABLE = """\
[tool.mutmut]
source_paths = ["workflow_scripts/"]
pytest_add_cli_args_test_selection = ["workflow_scripts/tests/"]
also_copy = [{also_copy}]
"""


def _write(root: Path, relative: str, text: str = "") -> None:
    """Write *text* at *relative* under *root*, creating parent directories."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _repository(
    root: Path,
    *,
    requires_python: str | None = ">=3.13",
    also_copy: tuple[str, ...] = ("conftest.py",),
    manifest: str | None = None,
    extra: dict[str, str] | None = None,
) -> Path:
    """Write a mutation-ready checkout and return its root."""
    project = '[project]\nname = "example"\n'
    if requires_python is not None:
        project += f'requires-python = "{requires_python}"\n'
    quoted = ", ".join(f'"{entry}"' for entry in also_copy)
    _write(
        root,
        "pyproject.toml",
        project + "\n" + MUTMUT_TABLE.format(also_copy=quoted),
    )
    _write(root, "conftest.py", "")
    _write(root, "workflow_scripts/example.py", "VALUE = 1\n")
    _write(root, "workflow_scripts/tests/test_example.py", "")
    for relative, text in (extra or {}).items():
        _write(root, relative, text)
    if manifest is not None:
        _write(root, ".github/mutation-preflight.toml", manifest)
    return root


@dc.dataclass(frozen=True, slots=True)
class Outcome:
    """What one preflight run reported.

    Attributes
    ----------
    exit_code : int
        The process status the run asked for.
    phases : dict[str, dict[str, object]]
        The emitted phase envelopes, keyed by phase name.
    result : str
        The final verdict line's value.
    stdout : str
        Everything the run printed, for assertions on ordering.

    """

    exit_code: int
    phases: dict[str, dict[str, object]]
    result: str
    stdout: str

    def findings(self, phase: str) -> str:
        """Return one phase's findings joined, for substring assertions."""
        found = self.phases.get(phase, {}).get("findings", [])
        return "\n".join(str(item) for item in found)

    def detail(self, phase: str) -> dict[str, object]:
        """Return one phase's evidence table."""
        return typ.cast("dict[str, object]", self.phases[phase]["detail"])


@pytest.fixture
def run_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> typ.Callable[..., Outcome]:
    """Return a callable that runs the preflight through its INPUT_* contract."""
    # A test that wants the job summary sets it after this fixture has run, so
    # the clear happens here rather than inside the callable.
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    def _run(root: Path, **inputs: str) -> Outcome:
        """Run the preflight against *root* with the given INPUT_* values."""
        monkeypatch.setenv("INPUT_REPOSITORY", str(root))
        monkeypatch.setenv("INPUT_OUTPUT_DIR", str(tmp_path / "out"))
        for key, value in inputs.items():
            monkeypatch.setenv(f"INPUT_{key.upper()}", value)
        capsys.readouterr()
        exit_code = 0
        try:
            preflight.app([])
        except SystemExit as error:
            exit_code = int(error.code or 0)
        captured = capsys.readouterr().out
        phases: dict[str, dict[str, object]] = {}
        result = ""
        for line in captured.splitlines():
            key, _, value = line.partition("=")
            if key == "mutation_preflight_phase":
                phase = typ.cast("dict[str, object]", json.loads(value))
                phases[str(phase["name"])] = phase
            if key == "mutation_preflight_result":
                result = value
        return Outcome(exit_code, phases, result, captured)

    return _run


@pytest.fixture
def install_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> typ.Callable[..., None]:
    """Return a callable that installs a fake POSIX command on ``PATH``."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", f"{bin_dir}:{local.env['PATH']}")
    monkeypatch.setitem(local.env, "PATH", f"{bin_dir}:{local.env['PATH']}")

    def _install(name: str, body: str) -> None:
        """Write an executable shim named *name* that runs *body*."""
        script = bin_dir / name
        script.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)

    return _install


@pytest.fixture
def empty_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Restrict ``PATH`` to an empty directory, so no real tool can answer."""
    bin_dir = tmp_path / "empty-bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setitem(local.env, "PATH", str(bin_dir))


class TestManifest:
    """The declaration is read, or refused, before anything is trusted."""

    def test_a_repository_declaring_nothing_is_a_clean_no_op(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """No declaration is not a failure: only the interpreter is checked."""
        outcome = run_preflight(_repository(tmp_path / "repo"), project_python="3.13")

        assert outcome.exit_code == 0, outcome.stdout
        assert outcome.result == "pass", outcome.stdout
        assert outcome.phases["manifest"]["status"] == "skipped"
        assert set(outcome.phases) == {"manifest", "interpreter"}

    def test_two_declarations_are_refused_rather_than_ranked(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """Which file binds the run must not depend on a precedence rule."""
        root = _repository(
            tmp_path / "repo",
            manifest='sandbox_paths = ["README.md"]\n',
            extra={"README.md": "# example\n"},
        )
        with (root / "pyproject.toml").open("a", encoding="utf-8") as handle:
            handle.write('\n[tool.mutation-preflight]\nsandbox_paths = ["README.md"]\n')

        outcome = run_preflight(root, project_python="3.13")

        assert outcome.exit_code == 1
        assert "keep one" in outcome.findings("manifest")
        assert set(outcome.phases) == {"manifest", "interpreter"}, (
            "a refused manifest must not be half-applied to later phases"
        )

    def test_an_unrecognised_key_is_refused(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A typo would otherwise read as a requirement that is being checked."""
        outcome = run_preflight(
            _repository(tmp_path / "repo", manifest='sandbox_path = ["README.md"]\n'),
            project_python="3.13",
        )

        assert outcome.exit_code == 1
        assert "sandbox_path" in outcome.findings("manifest")
        assert "supported keys" in outcome.findings("manifest")

    def test_sandbox_paths_are_refused_on_the_cargo_lane(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """cargo-mutants copies the working tree, so the requirement is vacuous."""
        outcome = run_preflight(
            _repository(tmp_path / "repo", manifest='sandbox_paths = ["README.md"]\n'),
            lane="cargo",
            project_python="3.13",
        )

        assert outcome.exit_code == 1
        assert "cargo-mutants copies the whole working tree" in outcome.findings(
            "manifest"
        )


class TestInterpreter:
    """The project interpreter is judged, and both runtimes are reported."""

    @pytest.mark.parametrize("interpreter", ["3.13", "3.13.5", "3.14"])
    def test_an_accepted_interpreter_passes(
        self,
        tmp_path: Path,
        run_preflight: typ.Callable[..., Outcome],
        interpreter: str,
    ) -> None:
        """A bare minor, a patch and the upper bound's own minor are accepted."""
        outcome = run_preflight(
            _repository(tmp_path / "repo", requires_python=">=3.12,<3.15"),
            project_python=interpreter,
            helper_python="3.13",
        )

        assert outcome.exit_code == 0, outcome.stdout
        assert outcome.phases["interpreter"]["status"] == "pass"

    @pytest.mark.parametrize(
        ("requirement", "interpreter"),
        [(">=3.14", "3.13"), ("<3.13", "3.13"), ("==3.12.*", "3.13")],
    )
    def test_a_rejected_interpreter_names_the_range(
        self,
        tmp_path: Path,
        run_preflight: typ.Callable[..., Outcome],
        requirement: str,
        interpreter: str,
    ) -> None:
        """A lower bound, an upper bound and another minor are all refused."""
        outcome = run_preflight(
            _repository(tmp_path / "repo", requires_python=requirement),
            project_python=interpreter,
            helper_python="3.13",
        )

        assert outcome.exit_code == 1
        assert "outside the project's requires-python" in outcome.findings(
            "interpreter"
        )
        assert requirement in outcome.findings("interpreter")

    def test_the_helper_and_project_runtimes_are_reported_separately(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A helper runtime inside the floor is not evidence about the project."""
        outcome = run_preflight(
            _repository(tmp_path / "repo", requires_python=">=3.14"),
            project_python="3.13",
            helper_python="3.13.5",
        )

        detail = outcome.detail("interpreter")
        assert detail["helper_python"] == "3.13.5"
        assert detail["project_python"] == "3.13"
        assert detail["same_runtime"] is False
        assert outcome.findings("interpreter").count("outside") == 1, (
            "only the project's range is violated, not the helper's"
        )

    def test_a_helper_runtime_below_its_floor_fails(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """The workflow's helper scripts need 3.13 regardless of the project."""
        outcome = run_preflight(
            _repository(tmp_path / "repo", requires_python=">=3.9"),
            project_python="3.12",
            helper_python="3.12.9",
        )

        assert outcome.exit_code == 1
        assert "below the helper floor 3.13" in outcome.findings("interpreter")

    def test_no_project_interpreter_is_reported_as_unknown(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A missing input cannot prove the project refuses its own range."""
        outcome = run_preflight(
            _repository(tmp_path / "repo"), project_python="", helper_python="3.13"
        )

        assert outcome.exit_code == 0, outcome.stdout
        assert outcome.phases["interpreter"]["status"] == "unknown"
        assert "not checked" in outcome.findings("interpreter")


class TestSandbox:
    """Declared paths are checked against what mutmut actually copies."""

    def test_a_missing_readme_is_named(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """The README is outside mutmut's copy set and absent, so it is named."""
        outcome = run_preflight(
            _repository(tmp_path / "repo", manifest='sandbox_paths = ["README.md"]\n'),
            project_python="3.13",
        )

        assert outcome.exit_code == 1
        assert "sandbox path 'README.md' is not in the checkout" in outcome.findings(
            "sandbox"
        )

    def test_a_readme_passes_once_also_copy_names_it(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """The declared path is satisfied by the copy entry that materialises it."""
        outcome = run_preflight(
            _repository(
                tmp_path / "repo",
                also_copy=("conftest.py", "README.md"),
                manifest='sandbox_paths = ["README.md"]\n',
                extra={"README.md": "# example\n"},
            ),
            project_python="3.13",
        )

        assert outcome.exit_code == 0, outcome.stdout
        assert outcome.phases["sandbox"]["status"] == "pass"
        assert outcome.detail("sandbox")["not_copied"] == []

    def test_a_hidden_workflow_tree_is_named_not_copied(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A suite that reads .github/workflows needs that tree in mutants/."""
        outcome = run_preflight(
            _repository(
                tmp_path / "repo",
                manifest='sandbox_paths = [".github/workflows/ci.yml"]\n',
                extra={".github/workflows/ci.yml": "jobs: {}\n"},
            ),
            project_python="3.13",
        )

        assert outcome.exit_code == 1
        assert ".github/workflows/ci.yml" in outcome.findings("sandbox")
        assert "also_copy" in outcome.findings("sandbox")

    def test_a_generator_sibling_is_named_not_copied(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A template outside source_paths is not copied just by existing."""
        outcome = run_preflight(
            _repository(
                tmp_path / "repo",
                manifest='sandbox_paths = ["templates/report.j2"]\n',
                extra={"templates/report.j2": "{}\n"},
            ),
            project_python="3.13",
        )

        assert outcome.exit_code == 1
        assert "templates/report.j2" in outcome.findings("sandbox")
        assert outcome.detail("sandbox")["not_copied"] == ["templates/report.j2"]

    def test_a_rust_toolchain_file_is_named(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A toolchain file the tests read is a sandbox requirement like any other."""
        outcome = run_preflight(
            _repository(
                tmp_path / "repo",
                manifest='sandbox_paths = ["rust-toolchain.toml"]\n',
                extra={"rust-toolchain.toml": '[toolchain]\nchannel = "stable"\n'},
            ),
            project_python="3.13",
        )

        assert outcome.exit_code == 1
        assert "rust-toolchain.toml" in outcome.findings("sandbox")

    def test_a_nested_fixture_is_named(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A fixture tree outside tests/ must be declared, not assumed."""
        outcome = run_preflight(
            _repository(
                tmp_path / "repo",
                manifest='sandbox_paths = ["fixtures/nested/data.json"]\n',
                extra={"fixtures/nested/data.json": "{}\n"},
            ),
            project_python="3.13",
        )

        assert outcome.exit_code == 1
        assert "fixtures/nested/data.json" in outcome.findings("sandbox")

    @pytest.mark.parametrize(
        ("declared", "expected"),
        [
            ("/etc/passwd", "is absolute"),
            ("../outside", "escapes the checkout"),
            ("target/debug/example", "build output"),
            (".venv/lib/x.py", "tool cache"),
        ],
    )
    def test_an_unusable_path_is_refused(
        self,
        tmp_path: Path,
        run_preflight: typ.Callable[..., Outcome],
        declared: str,
        expected: str,
    ) -> None:
        """Absolute, escaping, build-output and cache paths are all refused."""
        outcome = run_preflight(
            _repository(
                tmp_path / "repo",
                also_copy=("conftest.py", declared),
                manifest=f'sandbox_paths = ["{declared}"]\n',
                extra={declared.lstrip("./"): "x\n"}
                if not declared.startswith("/")
                else {},
            ),
            project_python="3.13",
        )

        assert outcome.exit_code == 1
        assert expected in outcome.findings("sandbox")

    def test_a_repository_without_mutmut_configuration_reports_unknown(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """Without a copy set the comparison cannot be made, and says so."""
        root = _repository(
            tmp_path / "repo", manifest='sandbox_paths = ["conftest.py"]\n'
        )
        (root / "pyproject.toml").write_text(
            '[project]\nname = "example"\n', encoding="utf-8"
        )

        outcome = run_preflight(root, project_python="3.13")

        assert outcome.exit_code == 0, outcome.stdout
        assert outcome.phases["sandbox"]["status"] == "unknown"
        assert "cannot be established" in outcome.findings("sandbox")


class TestCommands:
    """A required tool that is absent or wrong stops the run by name."""

    @POSIX_SHIMS_ONLY
    def test_a_missing_command_is_named(
        self,
        tmp_path: Path,
        run_preflight: typ.Callable[..., Outcome],
        empty_path: None,
    ) -> None:
        """An absent command is the failure, and it is named."""
        outcome = run_preflight(
            _repository(
                tmp_path / "repo",
                manifest='[[require_commands]]\nname = "mdtablefix"\n',
            ),
            project_python="3.13",
        )

        assert outcome.exit_code == 1
        assert "required command 'mdtablefix'" in outcome.findings("commands")
        assert "PATH" in outcome.findings("commands")

    @POSIX_SHIMS_ONLY
    def test_a_present_command_passes(
        self,
        tmp_path: Path,
        run_preflight: typ.Callable[..., Outcome],
        install_tool: typ.Callable[..., None],
    ) -> None:
        """The installed tool lets the same declaration pass."""
        install_tool("mold", 'echo "mold 2.41.0 (compatible with GNU ld)"')

        outcome = run_preflight(
            _repository(
                tmp_path / "repo",
                manifest='[[require_commands]]\nname = "mold"\nversion = "2.41"\n',
            ),
            project_python="3.13",
        )

        assert outcome.exit_code == 0, outcome.stdout
        assert outcome.phases["commands"]["status"] == "pass"

    @POSIX_SHIMS_ONLY
    def test_a_nearby_version_is_refused(
        self,
        tmp_path: Path,
        run_preflight: typ.Callable[..., Outcome],
        install_tool: typ.Callable[..., None],
    ) -> None:
        """``2.4`` must not accept ``2.41.0``, which is what a substring test does."""
        install_tool("mold", 'echo "mold 2.41.0"')

        outcome = run_preflight(
            _repository(
                tmp_path / "repo",
                manifest='[[require_commands]]\nname = "mold"\nversion = "2.4"\n',
            ),
            project_python="3.13",
        )

        assert outcome.exit_code == 1
        assert "does not report '2.4'" in outcome.findings("commands")
        assert "2.41.0" in outcome.findings("commands")


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

        outcome = run_preflight(
            _repository(tmp_path / "repo", manifest=manifest), project_python="3.13"
        )

        assert outcome.exit_code == 0, outcome.stdout
        printed = typ.cast("dict[str, object]", outcome.detail("checks")["collect"])
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

        outcome = run_preflight(
            _repository(tmp_path / "repo", manifest=manifest), project_python="3.13"
        )

        assert outcome.exit_code == 1
        found = outcome.findings("checks")
        assert "check 'collect' failed with exit code 2" in found
        assert "boom: no such option" in found

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

        outcome = run_preflight(
            _repository(tmp_path / "repo", manifest=manifest), project_python="3.13"
        )

        assert outcome.exit_code == 0, outcome.stdout
        log = tmp_path / "out" / "checks" / "collect.stdout.log"
        assert "collected 12 tests" in log.read_text(encoding="utf-8")

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

        outcome = run_preflight(
            _repository(tmp_path / "repo", manifest=manifest), project_python="3.13"
        )

        assert outcome.exit_code == 1
        assert "did not finish within 1 seconds" in outcome.findings("checks")


class TestNative:
    """A locked native dependency is named with its support boundary."""

    def _lock(self, version: str, source: str) -> str:
        """Return a ``uv.lock`` pinning one package at *version* from *source*."""
        return (
            "version = 1\n\n"
            "[[package]]\n"
            'name = "example"\nversion = "0.1.0"\n\n'
            "[[package]]\n"
            'name = "tei-rapporteur"\n'
            f'version = "{version}"\n'
            f"source = {{ {source} }}\n"
        )

    def test_an_unsupported_tuple_names_the_package_and_boundary(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """The pinned package and the range it supports are both reported."""
        manifest = '[native]\npackage = "tei-rapporteur"\nsupported_python = ">=3.14"\n'

        outcome = run_preflight(
            _repository(
                tmp_path / "repo",
                manifest=manifest,
                extra={
                    "uv.lock": self._lock(
                        "0.3.1", 'git = "https://example.invalid/tei.git?rev=abc123"'
                    )
                },
            ),
            project_python="3.13",
        )

        assert outcome.exit_code == 1
        found = outcome.findings("native")
        assert "tei-rapporteur's supported-python" in found
        assert ">=3.14" in found

    def test_unknown_support_stays_unknown(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """No declared range is reported as unknown, never as compatible."""
        manifest = '[native]\npackage = "tei-rapporteur"\n'

        outcome = run_preflight(
            _repository(
                tmp_path / "repo",
                manifest=manifest,
                extra={"uv.lock": self._lock("0.3.1", 'registry = "x"')},
            ),
            project_python="3.13",
        )

        assert outcome.exit_code == 0, outcome.stdout
        assert outcome.phases["native"]["status"] == "unknown"
        assert "unknown rather than established" in outcome.findings("native")

    def test_a_revision_that_the_lock_does_not_pin_is_refused(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """Dependency identity includes the revision, not just the name."""
        manifest = (
            "[native]\n"
            'package = "tei-rapporteur"\n'
            'revision = "def456"\n'
            'supported_python = ">=3.12"\n'
        )

        outcome = run_preflight(
            _repository(
                tmp_path / "repo",
                manifest=manifest,
                extra={
                    "uv.lock": self._lock(
                        "0.3.1", 'git = "https://example.invalid/tei.git?rev=abc123"'
                    )
                },
            ),
            project_python="3.13",
        )

        assert outcome.exit_code == 1
        assert "not the declared revision 'def456'" in outcome.findings("native")

    def test_a_failing_probe_reports_the_package_and_first_diagnostic(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A build or import probe failure is attributed to the package."""
        script = 'raise ImportError("no module named tei")'
        manifest = (
            "[native]\n"
            'package = "tei-rapporteur"\n'
            'supported_python = ">=3.12"\n'
            f"probe = ['{sys.executable}', '-c', '{script}']\n"
        )

        outcome = run_preflight(
            _repository(
                tmp_path / "repo",
                manifest=manifest,
                extra={"uv.lock": self._lock("0.3.1", 'registry = "x"')},
            ),
            project_python="3.13",
        )

        assert outcome.exit_code == 1
        found = outcome.findings("native")
        assert "the native probe for tei-rapporteur failed" in found
        assert "no module named tei" in found


class TestEnvelope:
    """The run publishes its phases and verdict with no mutants in existence."""

    def test_every_phase_is_emitted_in_order(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """The envelope is complete even when nothing has been mutated."""
        manifest = (
            'sandbox_paths = ["README.md"]\n'
            "[[require_commands]]\n"
            'name = "mold"\n'
            "[native]\n"
            'package = "tei-rapporteur"\n'
            "[[checks]]\n"
            'name = "collect"\n'
            f'command = ["{sys.executable}", "-c", "print(1)"]\n'
        )

        outcome = run_preflight(
            _repository(
                tmp_path / "repo",
                manifest=manifest,
                also_copy=("conftest.py", "README.md"),
                extra={
                    "README.md": "# example\n",
                    "uv.lock": TestNative()._lock("0.3.1", 'registry = "x"'),
                },
            ),
            project_python="3.13",
        )

        assert outcome.exit_code == 0, outcome.stdout
        order = [
            line.partition("=")[2]
            for line in outcome.stdout.splitlines()
            if line.startswith("mutation_preflight_phase=")
        ]
        names = [
            typ.cast("dict[str, object]", json.loads(item))["name"] for item in order
        ]
        assert names == [
            "manifest",
            "interpreter",
            "native",
            "sandbox",
            "commands",
            "checks",
        ]
        assert outcome.result == "pass"

    def test_the_report_is_written_without_mutation_results(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """The JSON report is the artefact a later stage reads, so it always exists."""
        outcome = run_preflight(_repository(tmp_path / "repo"), project_python="3.13")

        report = json.loads(
            (tmp_path / "out" / "mutation-preflight.json").read_text(encoding="utf-8")
        )
        assert report["result"] == "pass"
        assert [phase["name"] for phase in report["phases"]] == [
            "manifest",
            "interpreter",
        ]
        assert outcome.phases["manifest"]["status"] == "skipped"

    def test_the_step_summary_is_appended_when_the_job_asks(
        self,
        tmp_path: Path,
        run_preflight: typ.Callable[..., Outcome],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The Markdown summary rides on GITHUB_STEP_SUMMARY like the other gates."""
        summary = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

        outcome = run_preflight(_repository(tmp_path / "repo"), project_python="3.13")

        assert outcome.exit_code == 0, outcome.stdout
        rendered = summary.read_text(encoding="utf-8")
        assert "## Mutation environment preflight" in rendered
        assert "| interpreter | pass |" in rendered

    def test_an_unknown_lane_is_refused(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A lane this helper cannot check fails instead of borrowing another's."""
        outcome = run_preflight(
            _repository(tmp_path / "repo"), lane="stryker", project_python="3.13"
        )

        assert outcome.exit_code == 2
        assert outcome.result == "fail"
