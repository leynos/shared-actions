"""Tests covering metadata inspection helpers for validate-linux-packages."""

from __future__ import annotations

import importlib.util
import runpy
import sys
import typing as typ
from pathlib import Path

import pytest
from plumbum import local
from shared_actions_conftest import CMD_MOX_UNSUPPORTED

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers
    from types import ModuleType

    from shared_actions_conftest import CmdMox

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "validate.py"


@pytest.fixture
def validate_module() -> ModuleType:
    """Load the validate.py script as a module for testing."""
    spec = importlib.util.spec_from_file_location("validate_script", MODULE_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        message = "unable to load validate script"
        raise RuntimeError(message)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_validate_script_reexports_cli(validate_module: ModuleType) -> None:
    """The toplevel script should re-export the Cyclopts CLI objects."""
    cli_module = sys.modules.get("validate_cli")
    assert cli_module is not None
    assert validate_module.app is cli_module.app
    assert validate_module.main is cli_module.main
    assert validate_module.run is cli_module.run


def test_validate_script_executes_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Executing the script directly should invoke the CLI run helper."""
    module = sys.modules.get("validate_cli")
    if module is None:
        spec = importlib.util.spec_from_file_location(
            "validate_cli", MODULE_PATH.parent / "validate_cli.py"
        )
        if spec is None or spec.loader is None:  # pragma: no cover - defensive
            message = "unable to load validate_cli module"
            raise RuntimeError(message)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)

    called: list[object] = []

    def _marker() -> None:
        called.append(object())

    monkeypatch.setattr(module, "run", _marker)
    runpy.run_path(MODULE_PATH, run_name="__main__")
    assert called, "validate.py should call validate_cli.run() when executed"


@CMD_MOX_UNSUPPORTED
def test_inspect_deb_package_parses_metadata(
    validate_module: ModuleType,
    cmd_mox: CmdMox,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dpkg-deb metadata inspection extracts fields and payload paths."""
    package_path = tmp_path / "rust-toy-app_0.1.0-1_amd64.deb"
    package_path.write_bytes(b"")

    dpkg_expectations: dict[tuple[str, ...], str] = {
        (
            "-f",
            package_path.as_posix(),
            "Package",
            "Version",
            "Architecture",
        ): ("Package: rust-toy-app\nVersion: 0.1.0-1\nArchitecture: amd64\n"),
        ("-c", package_path.as_posix()): (
            "-rwxr-xr-x root/root 0 ./usr/bin/rust-toy-app\n"
        ),
    }

    def _dpkg_handler(invocation: object) -> tuple[str, str, int]:
        args = tuple(getattr(invocation, "args", ()))
        stdout = dpkg_expectations.pop(args, None)
        if stdout is None:
            message = f"unexpected dpkg-deb args: {args!r}"
            raise AssertionError(message)
        return stdout, "", 0

    cmd_mox.stub("dpkg-deb").runs(_dpkg_handler)

    cmd_mox.replay()
    shim_dir = cmd_mox.environment.shim_dir
    assert shim_dir is not None
    socket_path = cmd_mox.environment.socket_path
    assert socket_path is not None
    monkeypatch.setenv("CMOX_IPC_SOCKET", str(socket_path))
    monkeypatch.setitem(local.env, "CMOX_IPC_SOCKET", str(socket_path))

    metadata = validate_module.inspect_deb_package(
        local[str(shim_dir / "dpkg-deb")], package_path
    )

    assert not dpkg_expectations, "all dpkg-deb expectations must be consumed"
    cmd_mox.verify()

    assert metadata.name == "rust-toy-app"
    assert metadata.version == "0.1.0-1"
    assert metadata.architecture == "amd64"
    assert "/usr/bin/rust-toy-app" in metadata.files


@CMD_MOX_UNSUPPORTED
def test_inspect_rpm_package_parses_metadata(
    validate_module: ModuleType,
    cmd_mox: CmdMox,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RPM metadata inspection extracts fields and payload paths."""
    package_path = tmp_path / "rust-toy-app-0.1.0-1.x86_64.rpm"
    package_path.write_bytes(b"")

    rpm_expectations: dict[tuple[str, ...], str] = {
        ("-qip", package_path.as_posix()): (
            "Name        : rust-toy-app\n"
            "Version     : 0.1.0\n"
            "Release     : 1.el9\n"
            "Architecture: x86_64\n"
        ),
        ("-qlp", package_path.as_posix()): (
            "/usr/bin/rust-toy-app\n/usr/share/man/man1/rust-toy-app.1.gz\n"
        ),
    }

    def _rpm_handler(invocation: object) -> tuple[str, str, int]:
        args = tuple(getattr(invocation, "args", ()))
        stdout = rpm_expectations.pop(args, None)
        if stdout is None:
            message = f"unexpected rpm args: {args!r}"
            raise AssertionError(message)
        return stdout, "", 0

    cmd_mox.stub("rpm").runs(_rpm_handler)

    cmd_mox.replay()
    shim_dir = cmd_mox.environment.shim_dir
    assert shim_dir is not None
    socket_path = cmd_mox.environment.socket_path
    assert socket_path is not None
    monkeypatch.setenv("CMOX_IPC_SOCKET", str(socket_path))
    monkeypatch.setitem(local.env, "CMOX_IPC_SOCKET", str(socket_path))

    metadata = validate_module.inspect_rpm_package(
        local[str(shim_dir / "rpm")], package_path
    )

    assert not rpm_expectations, "all rpm expectations must be consumed"
    cmd_mox.verify()

    assert metadata.name == "rust-toy-app"
    assert metadata.version == "0.1.0"
    assert metadata.release == "1.el9"
    assert metadata.architecture == "x86_64"
    assert "/usr/bin/rust-toy-app" in metadata.files
    assert "/usr/share/man/man1/rust-toy-app.1.gz" in metadata.files
