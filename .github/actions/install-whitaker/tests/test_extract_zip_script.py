"""Tests for the zip extractor the action falls back to.

The Windows system `tar.exe` is bsdtar and handles the zip asset on the one
platform that ships one, so this script runs only where that binary is absent.
It still has to behave exactly like `tar --strip-components=1`, because the
install step looks for the executable directly under the extract directory, and
it has to refuse an archive that would write outside that directory, because it
runs on bytes fetched over the network before anything has inspected them.
"""

from __future__ import annotations

import importlib.util
import sys
import typing as typ
import zipfile

import pytest
from _action_manifest import ZIP_SCRIPT_PATH

if typ.TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    from pathlib import Path
    from types import ModuleType


def _load_extractor() -> ModuleType:
    """Import the shipped script by path, since it is not an installed module."""
    spec = importlib.util.spec_from_file_location("extract_zip", ZIP_SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


extractor = _load_extractor()


def _write_archive(path: Path, members: dict[str, bytes]) -> None:
    """Write a zip holding exactly ``members``."""
    with zipfile.ZipFile(path, "w") as package:
        for name, payload in members.items():
            package.writestr(name, payload)


def test_the_top_level_directory_is_stripped(tmp_path: Path) -> None:
    """A release archive's single directory must not survive extraction."""
    archive = tmp_path / "release.zip"
    _write_archive(archive, {"whitaker-installer-v0.2.7/installer.exe": b"payload"})
    destination = tmp_path / "out"
    destination.mkdir()

    written = extractor.extract(archive, destination)

    assert written == 1
    assert (destination / "installer.exe").read_bytes() == b"payload"
    assert not (destination / "whitaker-installer-v0.2.7").exists()


def test_nested_members_keep_their_remaining_structure(tmp_path: Path) -> None:
    """Only the first component is stripped, matching tar's behaviour."""
    archive = tmp_path / "release.zip"
    _write_archive(archive, {"top/lib/support.dll": b"library"})
    destination = tmp_path / "out"
    destination.mkdir()

    extractor.extract(archive, destination)

    assert (destination / "lib" / "support.dll").read_bytes() == b"library"


def test_the_extracted_file_is_executable(tmp_path: Path) -> None:
    """The installer must be runnable, whatever permissions the zip recorded.

    Zip carries Unix permissions only when the writer chose to record them, so
    the mode is set rather than read back out of the archive.
    """
    archive = tmp_path / "release.zip"
    _write_archive(archive, {"top/installer": b"payload"})
    destination = tmp_path / "out"
    destination.mkdir()

    extractor.extract(archive, destination)

    assert (destination / "installer").stat().st_mode & 0o111


def test_a_member_with_nothing_left_after_stripping_is_skipped(
    tmp_path: Path,
) -> None:
    """A bare top-level file contributes no output rather than failing."""
    archive = tmp_path / "release.zip"
    _write_archive(archive, {"loose": b"payload", "top/installer": b"payload"})
    destination = tmp_path / "out"
    destination.mkdir()

    written = extractor.extract(archive, destination)

    assert written == 1
    assert sorted(path.name for path in destination.iterdir()) == ["installer"]


@pytest.mark.parametrize(
    "member",
    [
        pytest.param("top/../../escape.txt", id="parent-traversal"),
        pytest.param("/absolute/escape.txt", id="absolute"),
        pytest.param("../escape.txt", id="leading-parent"),
    ],
)
def test_a_member_escaping_the_destination_is_refused(
    tmp_path: Path, member: str
) -> None:
    """Refuse to write outside the staging directory.

    The archive arrives over the network and is extracted before anything has
    looked inside it, so a member that resolves outside the destination stops
    extraction rather than being written and cleaned up afterwards.
    """
    archive = tmp_path / "release.zip"
    _write_archive(archive, {member: b"payload"})
    destination = tmp_path / "out"
    destination.mkdir()

    with pytest.raises(ValueError, match="refusing to extract"):
        extractor.extract(archive, destination)


def test_the_command_line_reports_an_archive_with_no_usable_members(
    tmp_path: Path,
) -> None:
    """An archive that yields nothing is an error, not a silent success.

    Exiting zero here would leave the install step looking for an executable
    that was never written, and reporting that as a missing file rather than
    as a bad archive.
    """
    archive = tmp_path / "release.zip"
    _write_archive(archive, {"loose": b"payload"})
    destination = tmp_path / "out"
    destination.mkdir()

    assert extractor.main([str(archive), str(destination)]) == 1


def test_the_command_line_reports_a_corrupt_archive(tmp_path: Path) -> None:
    """A file that is not a zip must fail with a message, not a traceback."""
    archive = tmp_path / "release.zip"
    archive.write_bytes(b"not a zip at all")
    destination = tmp_path / "out"
    destination.mkdir()

    assert extractor.main([str(archive), str(destination)]) == 1


def test_the_command_line_rejects_the_wrong_argument_count(tmp_path: Path) -> None:
    """Usage errors are distinguished from extraction failures."""
    assert extractor.main([str(tmp_path)]) == 2


def test_the_command_line_succeeds_on_a_release_shaped_archive(
    tmp_path: Path,
) -> None:
    """The success path returns zero, which is what the Bash arm relies on."""
    archive = tmp_path / "release.zip"
    _write_archive(archive, {"top/installer.exe": b"payload"})
    destination = tmp_path / "out"
    destination.mkdir()

    assert extractor.main([str(archive), str(destination)]) == 0
    assert (destination / "installer.exe").is_file()
