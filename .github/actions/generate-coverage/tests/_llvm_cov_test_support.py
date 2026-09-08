"""Helpers shared by the cargo-llvm-cov installer test modules.

The installer's tests are split across three modules by responsibility, so the
constants, archive builders and job-environment helper they share live here
rather than being duplicated. The ``install_llvm_cov_module`` fixture lives in
``conftest.py`` for the same reason ``install_nextest_module`` does: a fixture
declared there is visible to every module in this directory without an import
that reads as unused.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import tarfile
import typing as typ
import zipfile
from pathlib import Path

if typ.TYPE_CHECKING:
    from types import ModuleType

    import pytest
    from install_cargo_llvm_cov import ResolvedTool

RUNNERS = {
    "linux-x64": ("Linux", "X64"),
    "linux-arm64": ("Linux", "ARM64"),
    "macos-x64": ("macOS", "X64"),
    "macos-arm64": ("macOS", "ARM64"),
    "windows-x64": ("Windows", "X64"),
}

_FAKE_BINARY = b"#!/bin/sh\necho 'cargo-llvm-cov 0.9.0'\n"
_WRONG_VERSION_BINARY = b"#!/bin/sh\necho 'cargo-llvm-cov 0.6.24'\n"


ACTIONS_DIR = Path(__file__).resolve().parents[2]

#: Both actions ship the installer; the suite runs against each copy so the
#: ratchet-coverage one is executed rather than assumed identical.
INSTALLER_COPIES = {
    "generate-coverage": ACTIONS_DIR
    / "generate-coverage"
    / "scripts"
    / "install_cargo_llvm_cov.py",
    "ratchet-coverage": ACTIONS_DIR
    / "ratchet-coverage"
    / "scripts"
    / "install_cargo_llvm_cov.py",
}


def _load_installer(script: Path, name: str) -> ModuleType:
    """Load one installer copy from ``script`` under module name ``name``."""
    spec = importlib.util.spec_from_file_location(name, script)
    if spec is None or spec.loader is None:
        # A helper raises rather than asserts: an assertion here would report
        # as a failing test in whichever module happened to request the
        # fixture, rather than as the missing installer copy it is.
        message = f"could not load {name} from {script}"
        raise RuntimeError(message)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: A second payload, so a test can tell the requested member from a decoy by
#: content rather than only by name.
_DECOY_PAYLOAD = b"#!/bin/sh\necho 'decoy'\n"

#: Members an archive carries beside the one the manifest names. A correct
#: extraction writes none of them; ``extractall`` would write all of them.
_DECOY_MEMBERS = ("README.md", "completions/cargo-llvm-cov.bash")


def _tarball_with(members: dict[str, bytes]) -> bytes:
    """Return a gzip tarball holding each ``member`` payload, mode 0o755."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as package:
        for name, payload in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = 0o755
            package.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def _zip_with(members: dict[str, bytes]) -> bytes:
    """Return a zip archive holding each ``member`` payload."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w") as package:
        for name, payload in members.items():
            package.writestr(name, payload)
    return buffer.getvalue()


def _tarball_with_a_directory_member(member: str) -> bytes:
    """Return a gzip tarball whose ``member`` is a directory, not a file."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as package:
        info = tarfile.TarInfo(member)
        info.type = tarfile.DIRTYPE
        info.mode = 0o755
        package.addfile(info)
    return buffer.getvalue()


#: The two archive formats the manifest can name, with the builder for each.
_ARCHIVE_FORMATS = {
    "tar.gz": _tarball_with,
    "zip": _zip_with,
}


def _fake_tool(
    module: ModuleType, archive: bytes, *, extension: str, member: str
) -> ResolvedTool:
    """Return a ``ResolvedTool`` whose digest matches ``archive``."""
    url = (
        "https://github.com/taiki-e/cargo-llvm-cov/releases/download/v0.9.0/"
        f"cargo-llvm-cov-x86_64-unknown-linux-gnu.{extension}"
    )
    return typ.cast(
        "ResolvedTool",
        module.ResolvedTool(
            triple="x86_64-unknown-linux-gnu",
            url=url,
            sha256=hashlib.sha256(archive).hexdigest(),
            member=member,
            extension=extension,
            binary="cargo-llvm-cov",
            version_args=("llvm-cov", "--version"),
            expected_version="cargo-llvm-cov 0.9.0",
        ),
    )


def _write_fetch(archive: bytes) -> typ.Callable[[object, Path], None]:
    """Return a ``fetch`` stand-in that writes ``archive`` instead of downloading."""

    def fetch(_tool: object, destination: Path) -> None:
        """Write the canned archive to ``destination``."""
        destination.write_bytes(archive)

    return fetch


def _job_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[Path, Path, Path]:
    """Point CARGO_HOME, GITHUB_PATH and GITHUB_STEP_SUMMARY into ``tmp_path``."""
    cargo_home = tmp_path / "cargo"
    github_path = tmp_path / "github_path"
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("CARGO_HOME", str(cargo_home))
    monkeypatch.setenv("GITHUB_PATH", str(github_path))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("RUNNER_OS", "Linux")
    monkeypatch.setenv("RUNNER_ARCH", "X64")
    return cargo_home / "bin" / "cargo-llvm-cov", github_path, summary
