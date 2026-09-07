"""Verify the cargo-llvm-cov installer's entry point end to end.

Split from ``test_install_cargo_llvm_cov.py`` so that module keeps manifest
resolution alone. This module drives ``main`` across a real HTTP boundary
against a temporary manifest, and covers the reuse and replace decisions and
the bounded metrics the command boundary publishes. The tests and their
identifiers are unchanged by the move.
"""

from __future__ import annotations

import functools
import hashlib
import http.server
import threading
import typing as typ

import pytest
import typer
from _coverage_test_support import _exit_code
from _llvm_cov_test_support import (
    _FAKE_BINARY,
    _WRONG_VERSION_BINARY,
    _job_environment,
    _tarball_with,
)

if typ.TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType


def test_main_reuses_an_installed_binary_at_the_pinned_version(
    install_llvm_cov_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A binary already reporting the pinned version is kept and exported to PATH."""
    binary, github_path, summary = _job_environment(monkeypatch, tmp_path)
    binary.parent.mkdir(parents=True)
    binary.write_bytes(_FAKE_BINARY)
    binary.chmod(0o755)

    def fail_install(*_args: object, **_kwargs: object) -> None:
        """Fail the test if the installer tries to install."""
        message = "install must not run for a reused binary"
        raise AssertionError(message)

    monkeypatch.setattr(install_llvm_cov_module, "install", fail_install)

    install_llvm_cov_module.main()

    assert github_path.read_text(encoding="utf-8").strip() == str(binary.parent)
    assert "metric cargo-llvm-cov.install=reused" in summary.read_text(encoding="utf-8")


def test_main_replaces_an_installed_binary_at_another_version(
    install_llvm_cov_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A binary reporting a different version triggers a fresh install."""
    binary, _github_path, _summary = _job_environment(monkeypatch, tmp_path)
    binary.parent.mkdir(parents=True)
    binary.write_bytes(_WRONG_VERSION_BINARY)
    binary.chmod(0o755)
    calls: list[Path] = []

    monkeypatch.setattr(
        install_llvm_cov_module,
        "install",
        lambda _tool, destination, **_kwargs: calls.append(destination),
    )

    install_llvm_cov_module.main()

    assert calls == [binary]


class _ArchiveHandler(http.server.BaseHTTPRequestHandler):
    """Serve one archive at one path; anything else is a 404."""

    archive: typ.ClassVar[bytes] = b""
    path_served: typ.ClassVar[str] = ""

    def do_GET(self) -> None:
        if self.path != self.path_served:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.archive)))
        self.end_headers()
        self.wfile.write(self.archive)

    def log_message(self, *_args: object) -> None:
        """Keep the server quiet during the test."""
        return


@pytest.fixture
def archive_server() -> typ.Iterator[typ.Callable[[bytes, str], str]]:
    """Start a local HTTP server and return ``serve(archive, path) -> url``."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _ArchiveHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def serve(archive: bytes, path: str) -> str:
        """Publish ``archive`` at ``path`` and return its URL."""
        _ArchiveHandler.archive = archive
        _ArchiveHandler.path_served = path
        return f"http://127.0.0.1:{server.server_port}{path}"

    yield serve
    server.shutdown()
    server.server_close()


def test_entry_point_installs_from_a_manifest_over_http(
    install_llvm_cov_module: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    archive_server: typ.Callable[[bytes, str], str],
) -> None:
    """``main`` resolves, downloads, verifies, installs and exports, end to end.

    The manifest is a temporary one whose entry points at a local HTTP
    server, so the real download path runs without leaving the machine.
    """
    binary, github_path, summary = _job_environment(monkeypatch, tmp_path)
    archive = _tarball_with({"cargo-llvm-cov": _FAKE_BINARY})
    url = archive_server(
        archive, "/v0.9.0/cargo-llvm-cov-x86_64-unknown-linux-gnu.tar.gz"
    )
    manifest = tmp_path / "tool-manifest.toml"
    manifest.write_text(
        "schema = 1\n\n"
        "[[tool]]\n"
        'name = "cargo-llvm-cov"\n'
        f'version = "{install_llvm_cov_module.CARGO_LLVM_COV_VERSION}"\n'
        'binary = "cargo-llvm-cov"\n'
        'version-args = ["llvm-cov", "--version"]\n\n'
        "  [[tool.target]]\n"
        '  triple = "x86_64-unknown-linux-gnu"\n'
        f'  url = "{url}"\n'
        f'  sha256 = "{hashlib.sha256(archive).hexdigest()}"\n'
        '  member = "cargo-llvm-cov"\n'
        '  sidecar-verified = "absent"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(install_llvm_cov_module, "MANIFEST_PATH", manifest)
    monkeypatch.setattr(
        install_llvm_cov_module,
        "load_manifest",
        functools.partial(install_llvm_cov_module.load_manifest, manifest),
    )

    install_llvm_cov_module.main()

    assert binary.read_bytes() == _FAKE_BINARY
    assert binary.stat().st_mode & 0o111
    assert github_path.read_text(encoding="utf-8").strip() == str(binary.parent)
    metrics = summary.read_text(encoding="utf-8")
    assert "metric cargo-llvm-cov.resolve=ok" in metrics
    assert "metric cargo-llvm-cov.download=ok" in metrics
    assert "metric cargo-llvm-cov.archive-digest=ok" in metrics
    assert "metric cargo-llvm-cov.install=ok" in metrics


def test_entry_point_reports_a_resolution_failure_by_kind(
    install_llvm_cov_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unsupported runner exits 1 with a bounded resolve metric."""
    _binary, _github_path, summary = _job_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("RUNNER_OS", "Plan9")

    with pytest.raises(typer.Exit) as excinfo:
        install_llvm_cov_module.main()

    assert _exit_code(excinfo.value) == 1
    assert "metric cargo-llvm-cov.resolve=unsupported-runner" in summary.read_text(
        encoding="utf-8"
    )
