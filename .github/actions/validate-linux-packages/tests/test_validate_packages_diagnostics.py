"""Diagnostics-focused tests for validate_packages."""

from __future__ import annotations

import contextlib
import dataclasses
import typing as typ

import pytest
from plumbum.commands.processes import ProcessExecutionError

from test_support.sandbox import (
    DummySandbox,
    RaisingSandbox,
    SandboxContext,
    SandboxFailure,
)

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers
    from pathlib import Path
    from types import ModuleType
else:  # pragma: no cover - runtime fallback
    Path = typ.Any
    ModuleType = typ.Any


def _create_test_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    validate_packages_module: ModuleType,
) -> Path:
    package = tmp_path / "rust-toy-app_1.2.3-1_amd64.deb"
    package.write_bytes(b"payload")
    metadata = _build_metadata(validate_packages_module)
    monkeypatch.setattr(
        validate_packages_module,
        "inspect_deb_package",
        lambda *_: metadata,
    )
    monkeypatch.setattr(
        validate_packages_module.platform,
        "machine",
        lambda: "x86_64",
    )
    return package


def _create_raising_sandbox(
    tmp_path: Path,
    failure_command: tuple[str, ...],
    validate_packages_module: ModuleType,
    calls: list[tuple[tuple[str, ...], int | None]],
) -> RaisingSandbox:
    process_error = ProcessExecutionError(
        ["test", "-x", "/usr/bin/rust-toy-app"],
        1,
        "",
        "permission denied",
    )
    error = validate_packages_module.ValidationError("command failed")
    failure = SandboxFailure(
        command=failure_command,
        error=error,
        cause=process_error,
    )
    context = SandboxContext(
        root=tmp_path / "sandbox",
        calls=calls,
        failure=failure,
    )
    return RaisingSandbox(context)


def _run_validation(
    package: Path,
    validate_packages_module: ModuleType,
    sandbox: RaisingSandbox,
) -> str:
    with pytest.raises(validate_packages_module.ValidationError) as excinfo:
        validate_packages_module.validate_deb_package(
            dpkg_deb=object(),
            package_path=package,
            expected_name="rust-toy-app",
            expected_version="1.2.3",
            expected_deb_version="1.2.3-1",
            expected_arch="amd64",
            expected_paths=("/usr/bin/rust-toy-app",),
            executable_paths=("/usr/bin/rust-toy-app",),
            verify_command=("/usr/bin/rust-toy-app", "--version"),
            sandbox_factory=lambda: contextlib.nullcontext(sandbox),
        )
    return str(excinfo.value)


def _build_metadata(validate_packages_module: ModuleType) -> object:
    return validate_packages_module.DebMetadata(
        name="rust-toy-app",
        version="1.2.3-1",
        architecture="amd64",
        files={
            "/usr/bin/rust-toy-app",
            "/usr/share/doc/rust-toy-app",
        },
    )


def _exercise_install_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    validate_packages_module: ModuleType,
    failure_command: tuple[str, ...],
) -> tuple[str, list[tuple[tuple[str, ...], int | None]]]:
    package = _create_test_package(tmp_path, monkeypatch, validate_packages_module)
    calls: list[tuple[tuple[str, ...], int | None]] = []
    sandbox = _create_raising_sandbox(
        tmp_path,
        failure_command,
        validate_packages_module,
        calls,
    )
    message = _run_validation(package, validate_packages_module, sandbox)
    return message, calls


@dataclasses.dataclass(frozen=True)
class FailureScenario:
    """Describe an expected sandbox failure and its diagnostics."""

    command: tuple[str, ...]
    expected_message: str
    diagnostic_kind: str


FAILURE_SCENARIOS: tuple[FailureScenario, ...] = (
    FailureScenario(
        command=("dpkg", "-i", "/rust-toy-app_1.2.3-1_amd64.deb"),
        expected_message="dpkg installation failed",
        diagnostic_kind="none",
    ),
    FailureScenario(
        command=("test", "-e", "/usr/bin/rust-toy-app"),
        expected_message="expected path missing from sandbox payload",
        diagnostic_kind="path",
    ),
    FailureScenario(
        command=("test", "-x", "/usr/bin/rust-toy-app"),
        expected_message="expected path is not executable",
        diagnostic_kind="path",
    ),
    FailureScenario(
        command=("/usr/bin/rust-toy-app", "--version"),
        expected_message="sandbox verify command failed",
        diagnostic_kind="none",
    ),
)


def _assert_failure(
    scenario: FailureScenario,
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    validate_packages_module: ModuleType,
) -> None:
    message, calls = _exercise_install_failure(
        tmp_path, monkeypatch, validate_packages_module, scenario.command
    )
    assert scenario.expected_message in message

    diag_calls = [
        call
        for call, _timeout in calls
        if call
        and (
            call[0] in {"ls", "stat", "python3", "file", "sha256sum"}
            or (
                call[0] == scenario.command[-1]
                and len(call) > 1
                and call[1] == "--help"
            )
        )
    ]

    if scenario.diagnostic_kind == "path":
        path = scenario.command[-1]
        assert f"Path diagnostics for {path}" in message
        assert "- ls -ld" in message
        assert "- stat" in message
        assert "- file" in message
        assert "- sha256sum" in message
        assert f"- {path} --help" in message
        assert "- python os.access" in message
        assert "stderr: permission denied" in message
        assert diag_calls, "expected diagnostic commands to run"
        return

    assert "Path diagnostics for" not in message
    assert not diag_calls


def test_install_and_verify_wraps_validation_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    validate_packages_module: ModuleType,
) -> None:
    """Failures inside the sandbox surface descriptive error messages."""
    for scenario in FAILURE_SCENARIOS:
        _assert_failure(
            scenario,
            tmp_path=tmp_path,
            monkeypatch=monkeypatch,
            validate_packages_module=validate_packages_module,
        )


@pytest.fixture
def sandbox_with_python_fallback(
    tmp_path: Path, validate_packages_module: ModuleType
) -> tuple[DummySandbox, typ.Callable[..., str], list[tuple[tuple[str, ...], int | None]], str]:
    """Return sandbox + executor where ``test -x`` fails but fallback succeeds."""

    path = "/usr/bin/fallback-tool"
    calls: list[tuple[tuple[str, ...], int | None]] = []

    class FallbackSandbox(DummySandbox):
        def __init__(self) -> None:
            super().__init__(tmp_path / "sandbox-fallback", calls)

        def exec(self, *args: str, timeout: int | None = None) -> str:
            if tuple(args) == ("test", "-x", path):
                calls.append((tuple(args), timeout))
                raise validate_packages_module.ValidationError("not executable")
            return super().exec(*args, timeout=timeout)

    sandbox = FallbackSandbox()

    def exec_with_context(
        *args: str,
        context: str,
        timeout: int | None = None,
        diagnostics_fn: typ.Callable[[BaseException | None], str | None] | None = None,
    ) -> str:
        return validate_packages_module._exec_with_diagnostics(
            sandbox, args, context, timeout, diagnostics_fn
        )

    return sandbox, exec_with_context, calls, path


def test_validate_paths_executable_accepts_python_fallback(
    sandbox_with_python_fallback: tuple[
        DummySandbox,
        typ.Callable[..., str],
        list[tuple[tuple[str, ...], int | None]],
        str,
    ],
    validate_packages_module: ModuleType,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Ensure executables pass validation when the fallback succeeds."""

    sandbox, exec_fn, calls, path = sandbox_with_python_fallback
    caplog.set_level("INFO")

    validate_packages_module._validate_paths_executable(
        sandbox,
        (path,),
        exec_fn,
    )

    executed_commands = [command for command, _timeout in calls]
    assert ("test", "-x", path) in executed_commands
    assert any(command[0] == "python3" for command in executed_commands)
    assert any(
        "python os.access fallback succeeded" in record.getMessage()
        for record in caplog.records
    )
