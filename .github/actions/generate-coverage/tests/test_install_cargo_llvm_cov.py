"""Verify the manifest-driven cargo-llvm-cov installer.

The installer resolves its entry from ``.github/tool-manifest.toml`` with the
``install-tool`` resolver, so these tests hold the pinned version to the
manifest for every runner the resolver knows, exercise download, digest
verification, extraction and installation against a local archive, and check
that an installed binary at the pinned version is reused rather than replaced.
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import tarfile
import typing as typ
import zipfile

import pytest
from _coverage_test_support import _exit_code, _load_module

if typ.TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType

RUNNERS = {
    "linux-x64": ("Linux", "X64"),
    "linux-arm64": ("Linux", "ARM64"),
    "macos-x64": ("macOS", "X64"),
    "macos-arm64": ("macOS", "ARM64"),
    "windows-x64": ("Windows", "X64"),
}


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
    """A version the manifest does not list fails resolution."""
    with pytest.raises(BaseException) as excinfo:  # noqa: PT011 - typer.Exit
        install_llvm_cov_module.resolve_tool("0.0.1", runner=RUNNERS["linux-x64"])

    assert _exit_code(excinfo.value) == 1


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


_FAKE_BINARY = b"#!/bin/sh\necho 'cargo-llvm-cov 0.9.0'\n"


def _fake_tool(
    module: ModuleType, archive: bytes, *, extension: str, member: str
) -> object:
    url = (
        "https://github.com/taiki-e/cargo-llvm-cov/releases/download/v0.9.0/"
        f"cargo-llvm-cov-x86_64-unknown-linux-gnu.{extension}"
    )
    return module.ResolvedTool(
        triple="x86_64-unknown-linux-gnu",
        url=url,
        sha256=hashlib.sha256(archive).hexdigest(),
        member=member,
        extension=extension,
        binary="cargo-llvm-cov",
        version_args=("llvm-cov", "--version"),
        expected_version="cargo-llvm-cov 0.9.0",
    )


def _write_fetch(archive: bytes) -> typ.Callable[[object, Path], None]:
    def fetch(_tool: object, destination: Path) -> None:
        destination.write_bytes(archive)

    return fetch


@pytest.mark.skipif(
    not hasattr(__import__("os"), "fork"), reason="fake binary is a shell script"
)
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

    with pytest.raises(BaseException) as excinfo:  # noqa: PT011 - typer.Exit
        install_llvm_cov_module.install(tool, destination, fetch=_write_fetch(archive))

    assert _exit_code(excinfo.value) == 1
    assert destination.read_bytes() == b"previous"


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

    with pytest.raises(BaseException) as excinfo:  # noqa: PT011 - typer.Exit
        install_llvm_cov_module.download_archive(tool, destination)

    assert _exit_code(excinfo.value) == 1
    assert not destination.exists()


def test_main_reuses_an_installed_binary_at_the_pinned_version(
    install_llvm_cov_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A binary already reporting the pinned version is kept and exported to PATH."""
    cargo_home = tmp_path / "cargo"
    monkeypatch.setenv("CARGO_HOME", str(cargo_home))
    github_path = tmp_path / "github_path"
    monkeypatch.setenv("GITHUB_PATH", str(github_path))
    monkeypatch.setenv("RUNNER_OS", "Linux")
    monkeypatch.setenv("RUNNER_ARCH", "X64")
    binary = cargo_home / "bin" / "cargo-llvm-cov"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(_FAKE_BINARY)
    binary.chmod(0o755)

    def fail_install(*_args: object, **_kwargs: object) -> None:
        message = "install must not run for a reused binary"
        raise AssertionError(message)

    monkeypatch.setattr(install_llvm_cov_module, "install", fail_install)

    install_llvm_cov_module.main()

    assert github_path.read_text(encoding="utf-8").strip() == str(binary.parent)


def test_summary_metrics_are_bounded_lines(
    install_llvm_cov_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each metric is one ``metric key=value`` line appended to the summary."""
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    install_llvm_cov_module.emit_metric("cargo-llvm-cov.install=ok")

    assert summary.read_text(encoding="utf-8") == "metric cargo-llvm-cov.install=ok\n"
