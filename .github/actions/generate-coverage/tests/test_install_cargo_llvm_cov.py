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
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=list(INSTALLER_COPIES), ids=list(INSTALLER_COPIES))
def install_llvm_cov_module(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> ModuleType:
    """Return a freshly loaded installer copy with job-level side effects disabled."""
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.delenv("GITHUB_PATH", raising=False)
    monkeypatch.delenv("RUNNER_OS", raising=False)
    monkeypatch.delenv("RUNNER_ARCH", raising=False)
    if request.param == "generate-coverage":
        return _load_module(monkeypatch, "install_cargo_llvm_cov")
    return _load_installer(
        INSTALLER_COPIES[request.param], f"install_cargo_llvm_cov_{request.param}"
    )


def test_both_actions_ship_the_same_installer() -> None:
    """The two copies are byte-identical, so a fix in one cannot miss the other."""
    contents = {name: path.read_bytes() for name, path in INSTALLER_COPIES.items()}
    assert contents["generate-coverage"] == contents["ratchet-coverage"]


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


def test_manifest_with_another_schema_is_refused(
    install_llvm_cov_module: ModuleType,
) -> None:
    """A manifest schema this installer does not read fails closed, by kind."""
    manifest = {"schema": 2, "tool": []}

    with pytest.raises(install_llvm_cov_module.ToolResolutionError) as excinfo:
        install_llvm_cov_module.resolve_tool(
            manifest=manifest, runner=RUNNERS["linux-x64"]
        )

    assert excinfo.value.kind == "unsupported-schema"


def test_unreadable_manifest_is_a_typed_error(
    install_llvm_cov_module: ModuleType, tmp_path: Path
) -> None:
    """A missing manifest is reported by kind, not as a stack trace."""
    with pytest.raises(install_llvm_cov_module.ToolResolutionError) as excinfo:
        install_llvm_cov_module.load_manifest(tmp_path / "absent.toml")

    assert excinfo.value.kind == install_llvm_cov_module.MANIFEST_UNREADABLE


def _tarball_with(member: str, payload: bytes) -> bytes:
    """Return a gzip tarball holding ``payload`` at ``member``, mode 0o755."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as package:
        info = tarfile.TarInfo(member)
        info.size = len(payload)
        info.mode = 0o755
        package.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def _zip_with(member: str, payload: bytes) -> bytes:
    """Return a zip archive holding ``payload`` at ``member``."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w") as package:
        package.writestr(member, payload)
    return buffer.getvalue()


#: A second payload, so a test can tell the requested member from a decoy by
#: content rather than only by name.
_DECOY_PAYLOAD = b"#!/bin/sh\necho 'decoy'\n"

#: Members an archive carries beside the one the manifest names. A correct
#: extraction writes none of them; ``extractall`` would write all of them.
_DECOY_MEMBERS = ("README.md", "completions/cargo-llvm-cov.bash")


def _tarball_with_members(members: dict[str, bytes]) -> bytes:
    """Return a gzip tarball holding each ``member`` payload, mode 0o755."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as package:
        for name, payload in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = 0o755
            package.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def _zip_with_members(members: dict[str, bytes]) -> bytes:
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
    "tar.gz": _tarball_with_members,
    "zip": _zip_with_members,
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
    probe = install_llvm_cov_module.probe_version(destination, tool.version_args)
    assert install_llvm_cov_module.installed_at_pinned_version(
        probe, tool.expected_version
    )
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
    """A verified archive whose binary reports the wrong version is not published."""
    archive = _tarball_with("cargo-llvm-cov", _WRONG_VERSION_BINARY)
    tool = _fake_tool(
        install_llvm_cov_module, archive, extension="tar.gz", member="cargo-llvm-cov"
    )
    destination = tmp_path / "bin" / "cargo-llvm-cov"
    destination.parent.mkdir()
    destination.write_bytes(b"previous")

    with pytest.raises(typer.Exit) as excinfo:
        install_llvm_cov_module.install(tool, destination, fetch=_write_fetch(archive))

    assert _exit_code(excinfo.value) == 1
    assert destination.read_bytes() == b"previous"
    assert [p.name for p in destination.parent.iterdir()] == ["cargo-llvm-cov"]


def _installer_module() -> ModuleType:
    """Load the installer without pytest fixtures, for the property test.

    Hypothesis runs the test body many times per call and function-scoped
    fixtures would be shared across those examples, so the module is loaded
    directly here.
    """
    return _load_installer(
        INSTALLER_COPIES["generate-coverage"], "install_cargo_llvm_cov_property"
    )


_PROBE_STATES = st.sampled_from(["absent", "unrunnable", "reported"])


@given(state=_PROBE_STATES, version=st.one_of(st.none(), st.text(max_size=40)))
def test_only_a_reported_exact_version_counts_as_installed(
    state: str, version: str | None
) -> None:
    """``installed_at_pinned_version`` accepts one probe outcome and nothing else.

    A prefix match would accept ``cargo-llvm-cov 0.9.0-rc1`` or
    ``cargo-llvm-cov 0.9.01``; a substring match would accept a longer line
    that merely mentions the version; and an absent or unrunnable binary must
    never count, whatever ``version`` says.
    """
    module = _installer_module()
    probe = module.VersionProbe(state, version)

    outcome = module.installed_at_pinned_version(probe, "cargo-llvm-cov 0.9.0")

    assert outcome == (state == "reported" and version == "cargo-llvm-cov 0.9.0")
    assert probe.metric_state("cargo-llvm-cov 0.9.0") == (
        state if state != "reported" else ("pinned" if outcome else "other-version")
    )


@pytest.mark.parametrize(
    ("binary", "expected"),
    [
        pytest.param(None, ("absent", None), id="absent"),
        pytest.param(b"not executable", ("unrunnable", None), id="unrunnable"),
        pytest.param(_FAKE_BINARY, ("reported", "cargo-llvm-cov 0.9.0"), id="reported"),
    ],
)
def test_probe_version_reports_each_outcome_as_a_value(
    install_llvm_cov_module: ModuleType,
    tmp_path: Path,
    binary: bytes | None,
    expected: tuple[str, str | None],
) -> None:
    """A missing, unrunnable and reporting binary are three distinct probe states."""
    path = tmp_path / "cargo-llvm-cov"
    if binary is not None:
        path.write_bytes(binary)
        if binary == _FAKE_BINARY:
            path.chmod(0o755)

    probe = install_llvm_cov_module.probe_version(path, ("llvm-cov", "--version"))

    assert tuple(probe) == expected


def test_oversized_download_is_discarded(
    install_llvm_cov_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A response beyond the byte cap is cut off and removed."""
    monkeypatch.setattr(install_llvm_cov_module, "_MAX_ARCHIVE_BYTES", 16)

    class _Response(io.BytesIO):
        """A urlopen response that streams more bytes than the cap allows."""

        def __enter__(self) -> _Response:
            """Enter the response context."""
            return self

        def __exit__(self, *_args: object) -> None:
            """Close the response."""
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


def test_a_missing_resolver_is_a_typed_error_without_output(
    install_llvm_cov_module: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """An absent resolver raises the bounded kind and the query stays silent.

    Loading the resolver used to exit the process from inside the query, so
    a caller could not convert the failure into a metric.
    """
    with pytest.raises(install_llvm_cov_module.ToolResolutionError) as excinfo:
        install_llvm_cov_module.load_resolver(tmp_path / "absent.py")

    assert excinfo.value.kind == install_llvm_cov_module.RESOLVER_UNAVAILABLE
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_a_resolver_raising_on_import_is_a_typed_error(
    install_llvm_cov_module: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """A resolver whose module body raises is reported by kind, not as a traceback."""
    resolver = tmp_path / "resolve_tool.py"
    resolver.write_text('raise RuntimeError("resolver is broken")\n', encoding="utf-8")

    with pytest.raises(install_llvm_cov_module.ToolResolutionError) as excinfo:
        install_llvm_cov_module.load_resolver(resolver)

    assert excinfo.value.kind == install_llvm_cov_module.RESOLVER_UNAVAILABLE
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_resolve_tool_reports_a_failing_resolver_load_by_kind(
    install_llvm_cov_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``resolve_tool`` surfaces a resolver-load failure as its own typed error."""
    monkeypatch.setattr(
        install_llvm_cov_module, "RESOLVER_PATH", tmp_path / "absent.py"
    )

    with pytest.raises(install_llvm_cov_module.ToolResolutionError) as excinfo:
        install_llvm_cov_module.resolve_tool(runner=RUNNERS["linux-x64"])

    assert excinfo.value.kind == install_llvm_cov_module.RESOLVER_UNAVAILABLE


def test_resolve_tool_uses_an_injected_resolver(
    install_llvm_cov_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An injected resolver is used as given, so the query loads no file.

    ``RESOLVER_PATH`` points at nothing for the duration, which would fail the
    call were the dependency still resolved from disk.
    """
    monkeypatch.setattr(
        install_llvm_cov_module, "RESOLVER_PATH", tmp_path / "absent.py"
    )
    resolver = install_llvm_cov_module.load_resolver(
        Path(install_llvm_cov_module.__file__).resolve().parents[3]
        / "actions"
        / "install-tool"
        / "scripts"
        / "resolve_tool.py"
    )

    tool = install_llvm_cov_module.resolve_tool(
        runner=RUNNERS["linux-x64"], resolver=resolver
    )

    assert tool.expected_version == (
        f"cargo-llvm-cov {install_llvm_cov_module.CARGO_LLVM_COV_VERSION}"
    )


def test_main_reports_a_failing_resolver_load_as_a_metric(
    install_llvm_cov_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``main`` converts a resolver-load failure into the bounded metric and exit 1."""
    _binary, _github_path, summary = _job_environment(monkeypatch, tmp_path)
    monkeypatch.setattr(
        install_llvm_cov_module, "RESOLVER_PATH", tmp_path / "absent.py"
    )

    with pytest.raises(typer.Exit) as excinfo:
        install_llvm_cov_module.main()

    assert _exit_code(excinfo.value) == 1
    assert "metric cargo-llvm-cov.resolve=resolver-unavailable" in summary.read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize(
    "extension", list(_ARCHIVE_FORMATS), ids=list(_ARCHIVE_FORMATS)
)
def test_extraction_takes_only_the_named_member(
    install_llvm_cov_module: ModuleType, tmp_path: Path, extension: str
) -> None:
    """Only the manifest's member leaves the archive, whatever else it holds.

    The archive carries decoys beside the wanted member, so an implementation
    that unpacked everything would be caught here. Without them an
    ``extractall`` would satisfy every other test in this module, because a
    single-member archive makes the two strategies indistinguishable.
    """
    members = {"cargo-llvm-cov": _FAKE_BINARY} | dict.fromkeys(
        _DECOY_MEMBERS, _DECOY_PAYLOAD
    )
    archive_bytes = _ARCHIVE_FORMATS[extension](members)
    archive = tmp_path / f"cargo-llvm-cov.{extension}"
    archive.write_bytes(archive_bytes)
    tool = _fake_tool(
        install_llvm_cov_module,
        archive_bytes,
        extension=extension,
        member="cargo-llvm-cov",
    )
    destination = tmp_path / "bin" / "cargo-llvm-cov"
    destination.parent.mkdir()

    install_llvm_cov_module.extract_member(archive, tool, destination)

    assert destination.read_bytes() == _FAKE_BINARY
    assert [path.name for path in destination.parent.iterdir()] == ["cargo-llvm-cov"]
    assert not (tmp_path / "README.md").exists()
    assert not (tmp_path / "completions").exists()


@pytest.mark.parametrize(
    "extension", list(_ARCHIVE_FORMATS), ids=list(_ARCHIVE_FORMATS)
)
def test_extraction_refuses_an_archive_without_the_named_member(
    install_llvm_cov_module: ModuleType, tmp_path: Path, extension: str
) -> None:
    """A member the manifest names but the archive lacks is rejected outright.

    Both formats are covered: the missing-member path is written separately
    for zip and tar, so testing one leaves the other unguarded.
    """
    members = dict.fromkeys(_DECOY_MEMBERS, _DECOY_PAYLOAD)
    archive_bytes = _ARCHIVE_FORMATS[extension](members)
    archive = tmp_path / f"cargo-llvm-cov.{extension}"
    archive.write_bytes(archive_bytes)
    tool = _fake_tool(
        install_llvm_cov_module,
        archive_bytes,
        extension=extension,
        member="cargo-llvm-cov",
    )
    destination = tmp_path / "bin" / "cargo-llvm-cov"
    destination.parent.mkdir()

    with pytest.raises(ValueError, match="missing from"):
        install_llvm_cov_module.extract_member(archive, tool, destination)

    assert not destination.exists()


def test_extraction_refuses_a_tar_member_that_is_not_a_file(
    install_llvm_cov_module: ModuleType, tmp_path: Path
) -> None:
    """A tar entry with the member's name but a directory type is rejected.

    ``TarFile.extractfile`` returns ``None`` rather than raising for a
    non-regular entry, so an unchecked implementation would carry that
    ``None`` into the copy instead of failing here.
    """
    archive_bytes = _tarball_with_a_directory_member("cargo-llvm-cov")
    archive = tmp_path / "cargo-llvm-cov.tar.gz"
    archive.write_bytes(archive_bytes)
    tool = _fake_tool(
        install_llvm_cov_module,
        archive_bytes,
        extension="tar.gz",
        member="cargo-llvm-cov",
    )
    destination = tmp_path / "bin" / "cargo-llvm-cov"
    destination.parent.mkdir()

    with pytest.raises(ValueError, match="not a file"):
        install_llvm_cov_module.extract_member(archive, tool, destination)

    assert not destination.exists()
