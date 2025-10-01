"""Tests for the validate-linux-packages Cyclopts CLI orchestration."""

from __future__ import annotations

import contextlib
import importlib.util
import sys
import typing as typ
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
MODULE_PATH = SCRIPTS_DIR / "validate_cli.py"


@pytest.fixture
def validate_cli_module() -> object:
    """Load the validate_cli module under test."""
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.append(str(SCRIPTS_DIR))

    module = sys.modules.get("validate_cli")
    if module is not None:
        return module

    spec = importlib.util.spec_from_file_location("validate_cli", MODULE_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        message = "unable to load validate_cli module"
        raise RuntimeError(message)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # pragma: no cover - defensive
        message = f"failed to execute validate_cli module: {exc}"
        raise RuntimeError(message) from exc
    return module


def _write_package(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"archive")


def _prepare_polythene_stub(
    module: object,
    tmp_path: Path,
) -> tuple[list[tuple[str, str]], list[tuple[str, ...]]]:
    calls: list[tuple[str, str]] = []
    exec_calls: list[tuple[str, ...]] = []

    script_path = tmp_path / "polythene.py"
    script_path.write_text("#!/usr/bin/env python\n")

    def _default_path() -> Path:
        return script_path

    def _polythene_stub(
        polythene: Path,
        image: str,
        store: Path,
        *,
        timeout: int | None = None,
    ) -> typ.Iterable[object]:
        calls.append((image, store.as_posix()))
        store.mkdir(parents=True, exist_ok=True)
        root = store / "rootfs"
        root.mkdir(parents=True, exist_ok=True)

        @contextlib.contextmanager
        def _context() -> typ.Iterable[object]:
            class _Session:
                def __init__(self) -> None:
                    self.root = root

                def exec(self, *args: str, _timeout: int | None = None) -> None:
                    exec_calls.append(tuple(args))

            yield _Session()

        return _context()

    module.default_polythene_path = _default_path
    module.polythene_rootfs = _polythene_stub
    return calls, exec_calls


def test_build_config_splits_verify_command(
    validate_cli_module: object,
    tmp_path: Path,
) -> None:
    """Normalise verify command strings into tokenised argv entries."""
    module = validate_cli_module
    project_dir = tmp_path / "proj"
    packages_dir = project_dir / "dist"
    packages_dir.mkdir(parents=True, exist_ok=True)
    polythene_path = tmp_path / "polythene.py"
    polythene_path.write_text("#!/usr/bin/env python\n")

    inputs = module.ValidationInputs(
        project_dir=project_dir,
        bin_name="tool",
        version="1.0.0",
        formats=["deb"],
        verify_command=["/usr/bin/foo --version", "--flag"],
        polythene_path=polythene_path,
    )

    config = module._build_config(inputs)

    assert config.verify_command == ("/usr/bin/foo", "--version", "--flag")


def test_main_invokes_deb_validation(
    validate_cli_module: object,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Invoke Debian validation when deb format requested."""
    module = validate_cli_module
    project_dir = tmp_path / "proj"
    package_dir = project_dir / "dist"
    package_path = package_dir / "rust-toy-app_1.2.3-1_amd64.deb"
    _write_package(package_path)

    calls, exec_calls = _prepare_polythene_stub(module, tmp_path)

    monkeypatch.setattr(module, "get_command", lambda _name: object())

    recorded: dict[str, object] = {}

    def _validate_deb_package(
        _command: object,
        candidate_path: Path,
        *,
        expected_name: str,
        expected_version: str,
        expected_deb_version: str,
        expected_arch: str,
        expected_paths: typ.Iterable[str],
        executable_paths: typ.Iterable[str],
        verify_command: tuple[str, ...],
        sandbox_factory: object,
    ) -> None:
        recorded.update(
            {
                "path": candidate_path,
                "name": expected_name,
                "version": expected_version,
                "deb_version": expected_deb_version,
                "arch": expected_arch,
                "paths": tuple(expected_paths),
                "executables": tuple(executable_paths),
                "verify": verify_command,
            }
        )
        with contextlib.ExitStack() as stack:
            ctx = stack.enter_context(sandbox_factory())
            ctx.exec("test", "-e", "/usr/bin/rust-toy-app")

    monkeypatch.setattr(module, "validate_deb_package", _validate_deb_package)

    module.main(
        project_dir=project_dir,
        bin_name="rust-toy-app",
        version="v1.2.3",
        formats=["deb"],
        expected_paths=["/usr/share/man/man1/rust-toy-app.1.gz"],
    )

    out = capsys.readouterr().out
    assert "✓ validated Debian package" in out

    assert recorded["path"] == package_path
    assert recorded["name"] == "rust-toy-app"
    assert recorded["version"] == "1.2.3"
    assert recorded["deb_version"] == "1.2.3-1"
    assert recorded["arch"] == "amd64"
    assert recorded["paths"][0] == "/usr/bin/rust-toy-app"
    assert "/usr/share/man/man1/rust-toy-app.1.gz" in recorded["paths"]
    assert recorded["executables"] == ("/usr/bin/rust-toy-app",)
    assert recorded["verify"] == ()

    assert calls
    assert calls[0][0] == "docker.io/library/debian:bookworm"
    assert exec_calls
    assert exec_calls[0] == ("test", "-e", "/usr/bin/rust-toy-app")


def test_main_invokes_rpm_validation(
    validate_cli_module: object,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Invoke RPM validation when the rpm format is requested."""
    module = validate_cli_module
    project_dir = tmp_path / "proj"
    package_dir = project_dir / "dist"
    package_path = package_dir / "rust-toy-app-1.2.3-2.x86_64.rpm"
    _write_package(package_path)

    calls, exec_calls = _prepare_polythene_stub(module, tmp_path)

    monkeypatch.setattr(module, "get_command", lambda _name: object())

    recorded: dict[str, object] = {}

    def _validate_rpm_package(
        _command: object,
        candidate_path: Path,
        *,
        expected_name: str,
        expected_version: str,
        expected_release: str,
        expected_arch: str,
        expected_paths: typ.Iterable[str],
        executable_paths: typ.Iterable[str],
        verify_command: tuple[str, ...],
        sandbox_factory: object,
    ) -> None:
        recorded.update(
            {
                "path": candidate_path,
                "name": expected_name,
                "version": expected_version,
                "release": expected_release,
                "arch": expected_arch,
                "paths": tuple(expected_paths),
                "executables": tuple(executable_paths),
                "verify": verify_command,
            }
        )
        with contextlib.ExitStack() as stack:
            ctx = stack.enter_context(sandbox_factory())
            ctx.exec("/usr/bin/rust-toy-app", "--version")

    monkeypatch.setattr(module, "validate_rpm_package", _validate_rpm_package)

    module.main(
        project_dir=project_dir,
        bin_name="rust-toy-app",
        version="1.2.3",
        release="2",
        formats=["rpm"],
        verify_command=["/usr/bin/rust-toy-app", "--version"],
        executable_paths=["/usr/bin/rust-toy-app"],
    )

    assert recorded["path"] == package_path
    assert recorded["name"] == "rust-toy-app"
    assert recorded["version"] == "1.2.3"
    assert recorded["release"] == "2"
    assert recorded["arch"] == "x86_64"
    assert recorded["paths"][0] == "/usr/bin/rust-toy-app"
    assert recorded["executables"] == ("/usr/bin/rust-toy-app",)
    assert recorded["verify"] == ("/usr/bin/rust-toy-app", "--version")

    assert calls
    assert calls[0][0] == "docker.io/library/rockylinux:9"
    assert exec_calls
    assert exec_calls[0] == ("/usr/bin/rust-toy-app", "--version")
