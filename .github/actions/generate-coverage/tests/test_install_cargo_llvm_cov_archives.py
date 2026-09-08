"""Verify the cargo-llvm-cov installer's archive and installation handling.

Split from ``test_install_cargo_llvm_cov.py``, which keeps manifest
resolution, so neither module carries more responsibilities than the code
health rules allow. This module covers download bounds, digest verification,
selective extraction, atomic publication, version probing and the reuse
decision. The tests and their identifiers are unchanged by the move.
"""

from __future__ import annotations

import dataclasses
import io
import typing as typ

import pytest
import typer
from _coverage_test_support import _exit_code
from _llvm_cov_test_support import (
    _ARCHIVE_FORMATS,
    _DECOY_MEMBERS,
    _DECOY_PAYLOAD,
    _FAKE_BINARY,
    _WRONG_VERSION_BINARY,
    INSTALLER_COPIES,
    _fake_tool,
    _load_installer,
    _tarball_with,
    _tarball_with_a_directory_member,
    _write_fetch,
    _zip_with,
)
from hypothesis import given
from hypothesis import strategies as st

if typ.TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType


@pytest.mark.parametrize("extension", ["tar.gz", "zip"], ids=["tarball", "zip"])
def test_install_extracts_the_manifest_member_and_verifies_it(
    install_llvm_cov_module: ModuleType, tmp_path: Path, extension: str
) -> None:
    """A digest-verified archive installs exactly its member and reports the version."""
    archive = (
        _tarball_with({"cargo-llvm-cov": _FAKE_BINARY})
        if extension == "tar.gz"
        else _zip_with({"cargo-llvm-cov": _FAKE_BINARY})
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
    archive = _tarball_with({case.member: _FAKE_BINARY})
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
    archive = _tarball_with({"cargo-llvm-cov": _WRONG_VERSION_BINARY})
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
