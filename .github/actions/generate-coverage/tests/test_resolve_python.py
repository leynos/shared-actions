"""Tests for the coverage interpreter resolver.

The resolver decides which Python the coverage venv is built on and which
baseline cache family the ratchet reads. These tests drive its rules with an
injected environment mapping and a fake command runner, so no test touches
the process environment or needs a second interpreter installed.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import typing as typ
from pathlib import Path

import pytest

if typ.TYPE_CHECKING:  # pragma: no cover - type hints only
    from types import ModuleType

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "resolve_python.py"


@pytest.fixture(scope="module")
def resolver() -> ModuleType:
    """Load ``resolve_python`` from the action's scripts directory."""
    spec = importlib.util.spec_from_file_location("resolve_python", SCRIPT)
    assert spec is not None, "resolve_python.py must be importable"
    assert spec.loader is not None, "resolve_python.py must have a loader"
    module = importlib.util.module_from_spec(spec)
    sys.modules["resolve_python"] = module
    spec.loader.exec_module(module)
    return module


class FakeRunner:
    """Answer ``uv python find``/``install`` and the version probe from a table."""

    def __init__(
        self,
        *,
        installed: dict[str, str],
        installable: dict[str, str] | None = None,
        versions: dict[str, str] | None = None,
    ) -> None:
        """Record which specs uv finds, can install, and what each reports."""
        self.installed = dict(installed)
        self.installable = installable or {}
        self.versions = versions or {}
        self.calls: list[list[str]] = []

    def __call__(self, command: list[str]) -> tuple[int, str]:
        """Return the exit code and output the named command would produce."""
        self.calls.append(command)
        match command:
            case ["uv", "python", "find", spec]:
                path = self.installed.get(spec)
                return (0, f"{path}\n") if path else (2, "")
            case ["uv", "python", "install", spec] if spec in self.installable:
                self.installed[spec] = self.installable[spec]
                return 0, ""
            case ["uv", "python", "install", _]:
                return 1, ""
            case [python, "-c", _] if python in self.versions:
                return 0, f"{self.versions[python]}\n"
            case _:
                return 1, ""


class SourceCase(typ.NamedTuple):
    """One environment, an optional ``.python-version`` entry, and the winner."""

    env: dict[str, str]
    file_entry: str | None
    expected: tuple[str, str]


@pytest.mark.parametrize(
    "case",
    [
        SourceCase(
            {
                "INPUT_PYTHON_VERSION": "3.12",
                "UV_PYTHON": "3.13",
                "GC_PATH_PYTHON": "/p",
            },
            "3.11",
            ("3.12", "input"),
        ),
        SourceCase(
            {"UV_PYTHON": "3.13", "GC_PATH_PYTHON": "/p"}, "3.11", ("3.13", "UV_PYTHON")
        ),
        SourceCase({"GC_PATH_PYTHON": "/p"}, "3.11", ("3.11", ".python-version")),
        SourceCase(
            {"GC_PATH_PYTHON": "/usr/bin/python3"}, None, ("/usr/bin/python3", "PATH")
        ),
        SourceCase(
            {"INPUT_PYTHON_VERSION": "  ", "GC_PATH_PYTHON": "/p"}, None, ("/p", "PATH")
        ),
    ],
    ids=["input", "uv-python", "python-version-file", "path", "blank-input"],
)
def test_the_first_source_that_names_an_interpreter_wins(
    resolver: ModuleType, tmp_path: Path, case: SourceCase
) -> None:
    """Input, then ``UV_PYTHON``, then ``.python-version``, then ``PATH``."""
    version_file = tmp_path / ".python-version"
    if case.file_entry is not None:
        version_file.write_text(f"# pinned\n\n{case.file_entry}\n", encoding="utf-8")

    choice = resolver.choose_interpreter(case.env, version_file)

    assert (choice.spec, choice.source) == case.expected


def test_no_source_is_an_error(resolver: ModuleType, tmp_path: Path) -> None:
    """Refuse to fall back on uv's own discovery, which is what floated."""
    with pytest.raises(resolver.ResolutionError, match="no interpreter"):
        resolver.choose_interpreter({}, tmp_path / ".python-version")


def test_finding_an_interpreter_only_queries_uv(resolver: ModuleType) -> None:
    """The lookup answers from ``uv python find`` and never installs."""
    run = FakeRunner(
        installed={"3.13": "/py/3.13/bin/python3.13"},
        installable={"3.14": "/managed/python3.14"},
    )

    assert resolver.find_interpreter("3.13", run) == Path("/py/3.13/bin/python3.13")
    assert resolver.find_interpreter("3.14", run) is None
    assert [call[:3] for call in run.calls] == [["uv", "python", "find"]] * 2


def test_a_failed_lookup_finds_nothing_whatever_it_prints(
    resolver: ModuleType,
) -> None:
    """A non-zero ``uv python find`` is a miss even when it wrote a path."""

    def failing_find(_command: list[str]) -> tuple[int, str]:
        return 2, "/usr/bin/python3\n"

    assert resolver.find_interpreter("3.13", failing_find) is None


def test_installing_an_interpreter_makes_it_findable(resolver: ModuleType) -> None:
    """The install command downloads the requested Python for the next lookup."""
    run = FakeRunner(installed={}, installable={"3.13": "/managed/python3.13"})

    resolver.install_interpreter("3.13", run)

    assert resolver.find_interpreter("3.13", run) == Path("/managed/python3.13")


def test_an_uninstallable_interpreter_is_an_error(resolver: ModuleType) -> None:
    """Fail the step rather than build the venv on whatever uv finds."""
    with pytest.raises(resolver.ResolutionError, match="could not install"):
        resolver.install_interpreter("2.7", FakeRunner(installed={}))


class InstallCase(typ.NamedTuple):
    """Where the requested Python lives and how many installs that costs."""

    installed: dict[str, str]
    installable: dict[str, str]
    expected_installs: int


@pytest.mark.parametrize(
    "case",
    [
        InstallCase({"3.13": "/py/bin/python3.13"}, {}, 0),
        InstallCase({}, {"3.13": "/py/bin/python3.13"}, 1),
    ],
    ids=["present", "missing"],
)
def test_resolve_installs_only_a_missing_interpreter(
    resolver: ModuleType, tmp_path: Path, case: InstallCase
) -> None:
    """A runner that has the Python already downloads nothing."""
    run = FakeRunner(
        installed=case.installed,
        installable=case.installable,
        versions={"/py/bin/python3.13": "3.13"},
    )

    outputs = resolver.resolve({"UV_PYTHON": "3.13"}, tmp_path, run)

    assert outputs["python"] == "/py/bin/python3.13"
    installs = [call for call in run.calls if call[:3] == ["uv", "python", "install"]]
    assert len(installs) == case.expected_installs


def test_resolve_fails_when_an_installed_interpreter_stays_missing(
    resolver: ModuleType, tmp_path: Path
) -> None:
    """An install that uv then cannot find fails rather than guessing a path."""

    def install_succeeds_find_fails(command: list[str]) -> tuple[int, str]:
        return (0, "") if command[:3] == ["uv", "python", "install"] else (2, "")

    with pytest.raises(resolver.ResolutionError, match="could not find it"):
        resolver.resolve({"UV_PYTHON": "3.13"}, tmp_path, install_succeeds_find_fails)


@pytest.mark.parametrize(
    ("reported", "is_valid"),
    [("3.13", True), ("3.9", True), ("", False), ("3", False), ("three.13", False)],
)
def test_the_version_is_what_the_interpreter_reports(
    resolver: ModuleType, reported: str, *, is_valid: bool
) -> None:
    """Take major.minor from the interpreter, not from the request text."""
    run = FakeRunner(installed={}, versions={"/py": reported})
    if is_valid:
        assert resolver.major_minor(Path("/py"), run) == reported
    else:
        with pytest.raises(resolver.ResolutionError):
            resolver.major_minor(Path("/py"), run)


def test_resolve_publishes_the_path_version_and_key_segment(
    resolver: ModuleType, tmp_path: Path
) -> None:
    """The outputs name the interpreter the venv is built on and its key."""
    run = FakeRunner(
        installed={"3.13": "/py/bin/python3.13"},
        versions={"/py/bin/python3.13": "3.13"},
    )

    outputs = resolver.resolve({"UV_PYTHON": "3.13"}, tmp_path, run)

    assert outputs == {
        "python": "/py/bin/python3.13",
        "version": "3.13",
        "baseline-segment": "py3.13-",
        "source": "UV_PYTHON",
    }


def test_the_segment_changes_with_the_interpreter(
    resolver: ModuleType, tmp_path: Path
) -> None:
    """Two interpreters never share a baseline cache family."""
    segments = {
        resolver.resolve(
            {"INPUT_PYTHON_VERSION": version},
            tmp_path,
            FakeRunner(
                installed={version: f"/py{version}"},
                versions={f"/py{version}": version},
            ),
        )["baseline-segment"]
        for version in ("3.13", "3.14")
    }

    assert segments == {"py3.13-", "py3.14-"}


@pytest.mark.parametrize("request_text", ["/usr/bin/python3", "3.13.2", "cpython@3.13"])
def test_the_segment_comes_from_the_interpreter_not_the_request(
    resolver: ModuleType, tmp_path: Path, request_text: str
) -> None:
    """A path or a patch-level request still keys on the reported major.minor."""
    run = FakeRunner(
        installed={request_text: "/py/bin/python3.13"},
        versions={"/py/bin/python3.13": "3.13"},
    )

    outputs = resolver.resolve({"INPUT_PYTHON_VERSION": request_text}, tmp_path, run)

    assert outputs["baseline-segment"] == "py3.13-"


def test_main_publishes_the_outputs_and_a_bounded_notice(
    resolver: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The entry point writes the four outputs and names the rule it used."""
    github_output = tmp_path / "github_output"
    run = FakeRunner(
        installed={"3.13": "/py/bin/python3.13"},
        versions={"/py/bin/python3.13": "3.13"},
    )
    env = {"INPUT_PYTHON_VERSION": "3.13", "GITHUB_OUTPUT": str(github_output)}

    assert resolver.main(env, tmp_path, run) == 0
    assert github_output.read_text(encoding="utf-8").splitlines() == [
        "python=/py/bin/python3.13",
        "version=3.13",
        "baseline-segment=py3.13-",
        "source=input",
    ]
    assert (
        "::notice title=generate-coverage interpreter::python=3.13 source=input"
        in capsys.readouterr().out
    )


def test_main_fails_the_step_without_writing_outputs(
    resolver: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """With no interpreter to choose, the step fails and publishes nothing."""
    github_output = tmp_path / "github_output"
    env = {"GITHUB_OUTPUT": str(github_output)}

    assert resolver.main(env, tmp_path, FakeRunner(installed={})) == 1
    assert not github_output.exists()
    assert "::error title=generate-coverage interpreter::" in capsys.readouterr().err


def test_main_reports_an_unreadable_python_version_file(
    resolver: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A ``.python-version`` that cannot be decoded fails the step cleanly."""
    (tmp_path / ".python-version").write_bytes(b"\xff\xfe3.13\n")
    github_output = tmp_path / "github_output"
    env = {"GITHUB_OUTPUT": str(github_output)}

    assert resolver.main(env, tmp_path, FakeRunner(installed={})) == 1
    assert not github_output.exists()
    assert "could not read" in capsys.readouterr().err


@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0,
    reason="file modes do not deny reads to root or on Windows",
)
def test_an_unreadable_python_version_file_is_a_resolution_error(
    resolver: ModuleType, tmp_path: Path
) -> None:
    """A read the operating system refuses is reported, not raised raw."""
    version_file = tmp_path / ".python-version"
    version_file.write_text("3.13\n", encoding="utf-8")
    version_file.chmod(0)
    try:
        with pytest.raises(resolver.ResolutionError, match="could not read"):
            resolver.choose_interpreter({}, version_file)
    finally:
        version_file.chmod(0o600)
