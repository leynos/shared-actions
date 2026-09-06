"""Verify the manifest-driven cargo-llvm-cov installer.

The installer resolves its entry from ``.github/tool-manifest.toml`` with the
``install-tool`` resolver, so these tests hold the pinned version to the
manifest for every runner the resolver knows, exercise download, digest
verification, extraction and installation against local archives, drive the
whole entry point across a real HTTP boundary, and check that only a binary
reporting exactly the pinned version is reused.
"""

from __future__ import annotations

import dataclasses
import functools
import hashlib
import http.server
import importlib.util
import io
import tarfile
import threading
import typing as typ
import zipfile
from pathlib import Path

import pytest
import typer
from _coverage_test_support import _exit_code, _load_module
from hypothesis import given
from hypothesis import strategies as st

if typ.TYPE_CHECKING:
    from types import ModuleType

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


@pytest.fixture
def install_llvm_cov_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Return a freshly loaded installer with job-level side effects disabled."""
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.delenv("GITHUB_PATH", raising=False)
    monkeypatch.delenv("RUNNER_OS", raising=False)
    monkeypatch.delenv("RUNNER_ARCH", raising=False)
    return _load_module(monkeypatch, "install_cargo_llvm_cov")


@pytest.mark.parametrize("runner", list(RUNNERS.values()), ids=list(RUNNERS))
def test_pinned_version_resolves_from_the_manifest_for_every_runner(
    install_llvm_cov_module: ModuleType, runner: tuple[str, str]
) -> None:
    """The version the script pins is in the manifest for each supported runner."""
    tool = install_llvm_cov_module.resolve_tool(runner=runner)

    version = install_llvm_cov_module.CARGO_LLVM_COV_VERSION
    assert f"/v{version}/" in tool.url, tool.url
    assert tool.expected_version == f"cargo-llvm-cov {version}"
    assert tool.version_args == ("llvm-cov", "--version")
    assert len(tool.sha256) == 64
    assert tool.binary.endswith(".exe") == (runner[0] == "Windows")


def test_manifest_pin_is_the_layout_aware_release(
    install_llvm_cov_module: ModuleType,
) -> None:
    """The pin is at least 0.9.0, the first release reading Cargo's new layout.

    cargo 1.100 nightlies place test executables under
    ``debug/build/<package>/<hash>/out`` and 0.6.24 searched ``debug/deps``,
    failing with "not found object files" after every test passed.
    """
    major, minor, _patch = (
        int(part) for part in install_llvm_cov_module.CARGO_LLVM_COV_VERSION.split(".")
    )
    assert (major, minor) >= (0, 9)


def test_unknown_version_is_refused_rather_than_floated(
    install_llvm_cov_module: ModuleType,
) -> None:
    """A version the manifest does not list raises a typed resolution error."""
    with pytest.raises(install_llvm_cov_module.ToolResolutionError) as excinfo:
        install_llvm_cov_module.resolve_tool("0.0.1", runner=RUNNERS["linux-x64"])

    assert excinfo.value.kind == "unknown-version"


def test_unreadable_manifest_is_a_typed_error(
    install_llvm_cov_module: ModuleType, tmp_path: Path
) -> None:
    """A missing manifest is reported by kind, not as a stack trace."""
    with pytest.raises(install_llvm_cov_module.ToolResolutionError) as excinfo:
        install_llvm_cov_module.load_manifest(tmp_path / "absent.toml")

    assert excinfo.value.kind == install_llvm_cov_module.MANIFEST_UNREADABLE


def _tarball_with(member: str, payload: bytes) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as package:
        info = tarfile.TarInfo(member)
        info.size = len(payload)
        info.mode = 0o755
        package.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def _zip_with(member: str, payload: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w") as package:
        package.writestr(member, payload)
    return buffer.getvalue()


def _fake_tool(
    module: ModuleType,
    archive: bytes,
    *,
    extension: str,
    member: str,
    url: str | None = None,
) -> ResolvedTool:
    if url is None:
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
    def fetch(_tool: object, destination: Path) -> None:
        destination.write_bytes(archive)

    return fetch


@pytest.mark.parametrize("extension", ["tar.gz", "zip"], ids=["tarball", "zip"])
def test_install_extracts_the_manifest_member_and_verifies_it(
    install_llvm_cov_module: ModuleType, tmp_path: Path, extension: str
) -> None:
    """A digest-verified archive installs exactly its member and reports the version."""
    archive = (
        _tarball_with("cargo-llvm-cov", _FAKE_BINARY)
        if extension == "tar.gz"
        else _zip_with("cargo-llvm-cov", _FAKE_BINARY)
    )
    tool = _fake_tool(
        install_llvm_cov_module, archive, extension=extension, member="cargo-llvm-cov"
    )
    destination = tmp_path / "bin" / "cargo-llvm-cov"

    install_llvm_cov_module.install(tool, destination, fetch=_write_fetch(archive))

    assert destination.read_bytes() == _FAKE_BINARY
    assert destination.stat().st_mode & 0o111
    assert install_llvm_cov_module.installed_at_pinned_version(destination, tool)
    assert [p.name for p in destination.parent.iterdir()] == ["cargo-llvm-cov"], (
        "the staging directory must not outlive the install"
    )


@dataclasses.dataclass(frozen=True)
class _RejectedArchive:
    """One way a downloaded archive can be unusable."""

    member: str
    tamper_digest: bool


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            _RejectedArchive(member="cargo-llvm-cov", tamper_digest=True),
            id="digest-mismatch",
        ),
        pytest.param(
            _RejectedArchive(member="some-other-binary", tamper_digest=False),
            id="missing-member",
        ),
    ],
)
def test_rejected_archive_fails_and_preserves_the_existing_binary(
    install_llvm_cov_module: ModuleType, tmp_path: Path, case: _RejectedArchive
) -> None:
    """A tampered or malformed archive is an error that leaves the binary alone."""
    archive = _tarball_with(case.member, _FAKE_BINARY)
    tool = _fake_tool(
        install_llvm_cov_module, archive, extension="tar.gz", member="cargo-llvm-cov"
    )
    if case.tamper_digest:
        tool = tool._replace(sha256="0" * 64)
    destination = tmp_path / "bin" / "cargo-llvm-cov"
    destination.parent.mkdir()
    destination.write_bytes(b"previous")

    with pytest.raises(typer.Exit) as excinfo:
        install_llvm_cov_module.install(tool, destination, fetch=_write_fetch(archive))

    assert _exit_code(excinfo.value) == 1
    assert destination.read_bytes() == b"previous"


def test_installed_binary_reporting_another_version_fails_the_install(
    install_llvm_cov_module: ModuleType, tmp_path: Path
) -> None:
    """A verified archive whose binary reports the wrong version is still a failure."""
    archive = _tarball_with("cargo-llvm-cov", _WRONG_VERSION_BINARY)
    tool = _fake_tool(
        install_llvm_cov_module, archive, extension="tar.gz", member="cargo-llvm-cov"
    )
    destination = tmp_path / "bin" / "cargo-llvm-cov"

    with pytest.raises(typer.Exit) as excinfo:
        install_llvm_cov_module.install(tool, destination, fetch=_write_fetch(archive))

    assert _exit_code(excinfo.value) == 1


def _installer_module() -> ModuleType:
    """Load the installer without pytest fixtures, for the property test.

    Hypothesis runs the test body many times per call and function-scoped
    fixtures would be shared across those examples, so the module is loaded
    directly here.
    """
    script = (
        Path(__file__).resolve().parents[1] / "scripts" / "install_cargo_llvm_cov.py"
    )
    spec = importlib.util.spec_from_file_location(
        "install_cargo_llvm_cov_property", script
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@given(reported=st.one_of(st.none(), st.text(max_size=40)))
def test_only_the_exact_expected_version_counts_as_installed(
    reported: str | None,
) -> None:
    """``installed_at_pinned_version`` accepts the exact version line and nothing else.

    A prefix match would accept ``cargo-llvm-cov 0.9.0-rc1`` or
    ``cargo-llvm-cov 0.9.01``; a substring match would accept a longer line
    that merely mentions the version.
    """
    module = _installer_module()
    tool = _fake_tool(module, b"", extension="tar.gz", member="cargo-llvm-cov")

    class _Present:
        def is_file(self) -> bool:
            return True

    module.reported_version = lambda _binary, _args: reported

    outcome = module.installed_at_pinned_version(typ.cast("Path", _Present()), tool)

    assert outcome == (reported == "cargo-llvm-cov 0.9.0")


def test_oversized_download_is_discarded(
    install_llvm_cov_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A response beyond the byte cap is cut off and removed."""
    monkeypatch.setattr(install_llvm_cov_module, "_MAX_ARCHIVE_BYTES", 16)

    class _Response(io.BytesIO):
        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *_args: object) -> None:
            self.close()

    monkeypatch.setattr(
        install_llvm_cov_module.urllib.request,
        "urlopen",
        lambda *_a, **_k: _Response(b"x" * 64),
    )
    tool = _fake_tool(
        install_llvm_cov_module, b"", extension="tar.gz", member="cargo-llvm-cov"
    )
    destination = tmp_path / tool.filename

    with pytest.raises(typer.Exit) as excinfo:
        install_llvm_cov_module.download_archive(tool, destination)

    assert _exit_code(excinfo.value) == 1
    assert not destination.exists()


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


def test_main_reuses_an_installed_binary_at_the_pinned_version(
    install_llvm_cov_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A binary already reporting the pinned version is kept and exported to PATH."""
    binary, github_path, summary = _job_environment(monkeypatch, tmp_path)
    binary.parent.mkdir(parents=True)
    binary.write_bytes(_FAKE_BINARY)
    binary.chmod(0o755)

    def fail_install(*_args: object, **_kwargs: object) -> None:
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
        return


@pytest.fixture
def archive_server() -> typ.Iterator[typ.Callable[[bytes, str], str]]:
    """Start a local HTTP server and return ``serve(archive, path) -> url``."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _ArchiveHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def serve(archive: bytes, path: str) -> str:
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
    archive = _tarball_with("cargo-llvm-cov", _FAKE_BINARY)
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
