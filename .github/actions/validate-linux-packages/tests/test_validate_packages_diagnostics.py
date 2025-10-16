"""Diagnostics-focused tests for validate_packages."""

from __future__ import annotations

import contextlib
import typing as typ

import pytest
from plumbum.commands.processes import ProcessExecutionError

from test_support.sandbox import RaisingSandbox

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers
    from pathlib import Path
    from types import ModuleType
else:  # pragma: no cover - runtime fallback
    Path = typ.Any
    ModuleType = typ.Any


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

    process_error = ProcessExecutionError(
        ["test", "-x", "/usr/bin/rust-toy-app"],
        1,
        "",
        "permission denied",
    )
    error = validate_packages_module.ValidationError("command failed")
    calls: list[tuple[tuple[str, ...], int | None]] = []
    sandbox = RaisingSandbox(
        tmp_path / "sandbox",
        calls,
        failure_command=failure_command,
        error=error,
        cause=process_error,
    )

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

    return str(excinfo.value), calls


@pytest.mark.parametrize(
    ("failure_command", "expected_message", "diagnostic_kind"),
    [
        (
            ("dpkg", "-i", "/rust-toy-app_1.2.3-1_amd64.deb"),
            "dpkg installation failed",
            "none",
        ),
        (
            ("test", "-e", "/usr/bin/rust-toy-app"),
            "expected path missing from sandbox payload",
            "path",
        ),
        (
            ("test", "-x", "/usr/bin/rust-toy-app"),
            "expected path is not executable",
            "path",
        ),
        (
            ("/usr/bin/rust-toy-app", "--version"),
            "sandbox verify command failed",
            "none",
        ),
    ],
)
def test_install_and_verify_wraps_validation_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    validate_packages_module: ModuleType,
    failure_command: tuple[str, ...],
    expected_message: str,
    diagnostic_kind: str,
) -> None:
    """Failures inside the sandbox surface descriptive error messages."""
    message, calls = _exercise_install_failure(
        tmp_path, monkeypatch, validate_packages_module, failure_command
    )
    assert expected_message in message

    path = "/usr/bin/rust-toy-app"
    diag_calls = [
        call
        for call, _timeout in calls
        if call
        and (
            call[0] in {"ls", "stat", "python3", "file", "sha256sum"}
            or (call[0] == path and len(call) > 1 and call[1] == "--help")
        )
    ]

    if diagnostic_kind == "path":
        path = failure_command[-1]
        assert f"Path diagnostics for {path}" in message
        assert "- ls -ld" in message
        assert "- stat" in message
        assert "- file" in message
        assert "- sha256sum" in message
        assert f"- {path} --help" in message
        assert "- python os.access" in message
        assert "stderr: permission denied" in message
        assert diag_calls, "expected diagnostic commands to run"
    else:
        assert "Path diagnostics for" not in message
        assert not diag_calls
