"""`FilesystemBinaryStore`: the one adapter that touches the disk.

Real temporary directories drive every case that a real filesystem can be made
to produce. Only the two failures a filesystem will not produce on demand - a
write and a `chmod` that fail after the staged file exists - inject an
`OSError`, and they do so against this adapter, not against the policy.
"""

from __future__ import annotations

import os
import typing as typ
from pathlib import Path

import pytest
from makeutil_errors import StoreError
from makeutil_store import FilesystemBinaryStore
from makeutil_verify import sha256_hex

_DATA = b"pretend this is a static makeutil binary\n"


class TestDigest:
    """`digest` reports what is stored, or that nothing is."""

    def test_an_absent_file_has_no_digest(self, tmp_path: Path) -> None:
        """Nothing to reuse is `None`, not an error."""
        assert FilesystemBinaryStore(tmp_path / "makeutil").digest() is None

    def test_a_file_reports_its_sha256(self, tmp_path: Path) -> None:
        """The digest is of the bytes on disk."""
        target = tmp_path / "makeutil"
        target.write_bytes(_DATA)

        assert FilesystemBinaryStore(target).digest() == sha256_hex(_DATA)

    def test_a_directory_at_the_path_is_not_a_reusable_file(
        self, tmp_path: Path
    ) -> None:
        """Something that is not a file has no digest."""
        assert FilesystemBinaryStore(tmp_path).digest() is None

    def test_an_unreadable_file_is_a_store_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An `OSError` reading the entry becomes a `StoreError`."""
        target = tmp_path / "makeutil"
        target.write_bytes(_DATA)

        def _failing(self: Path, *_args: object) -> typ.NoReturn:
            message = "simulated read failure"
            raise OSError(message)

        monkeypatch.setattr(Path, "read_bytes", _failing)

        with pytest.raises(StoreError, match="could not read"):
            FilesystemBinaryStore(target).digest()


class TestInstall:
    """`install` stages and replaces atomically, or fails leaving nothing."""

    def test_the_binary_lands_executable_and_creates_its_directory(
        self, tmp_path: Path
    ) -> None:
        """A missing parent directory is made by the install, not before."""
        target = tmp_path / "nested" / "bin" / "makeutil"

        FilesystemBinaryStore(target).install(_DATA)

        assert target.read_bytes() == _DATA
        assert os.access(target, os.X_OK)
        assert [path.name for path in target.parent.iterdir()] == ["makeutil"]

    def test_a_directory_that_cannot_be_created_is_a_store_error(
        self, tmp_path: Path
    ) -> None:
        """A `bin-dir` beneath a regular file cannot be made."""
        blocker = tmp_path / "file"
        blocker.write_text("not a directory")

        with pytest.raises(StoreError, match="could not install"):
            FilesystemBinaryStore(blocker / "bin" / "makeutil").install(_DATA)

    def test_a_target_that_is_a_directory_is_a_store_error_and_leaves_no_stage(
        self, tmp_path: Path
    ) -> None:
        """The replace fails for real; the staged file must not be left behind."""
        target = tmp_path / "makeutil"
        target.mkdir()

        with pytest.raises(StoreError):
            FilesystemBinaryStore(target).install(_DATA)

        assert [path.name for path in tmp_path.iterdir()] == ["makeutil"]

    @pytest.mark.parametrize("failing_step", ["write", "chmod"])
    def test_a_write_or_chmod_failure_is_a_store_error_and_leaves_no_stage(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failing_step: str
    ) -> None:
        """A failure after the staged file exists removes it again."""
        target = tmp_path / "bin" / "makeutil"
        real_fdopen = os.fdopen

        class _FailingHandle:
            def __init__(self, handle: typ.IO[bytes]) -> None:
                self._handle = handle

            def __enter__(self) -> typ.Self:
                return self

            def __exit__(self, *_exc_info: object) -> None:
                self._handle.close()

            def write(self, _data: bytes) -> int:
                message = "simulated write failure"
                raise OSError(message)

        def _failing_chmod(self: Path, *_args: object) -> typ.NoReturn:
            message = "simulated chmod failure"
            raise OSError(message)

        if failing_step == "write":
            monkeypatch.setattr(
                os, "fdopen", lambda fd, mode: _FailingHandle(real_fdopen(fd, mode))
            )
        else:
            monkeypatch.setattr(Path, "chmod", _failing_chmod)

        with pytest.raises(StoreError):
            FilesystemBinaryStore(target).install(_DATA)

        assert list(target.parent.iterdir()) == []


class TestMakeExecutableAndDiscard:
    """The two small effects the CLI and the cache path use."""

    def test_make_executable_sets_the_bit(self, tmp_path: Path) -> None:
        """A cache restore drops the bit; this restores it."""
        target = tmp_path / "makeutil"
        target.write_bytes(_DATA)
        target.chmod(0o644)

        FilesystemBinaryStore(target).make_executable()

        assert os.access(target, os.X_OK)

    def test_make_executable_on_an_absent_file_is_a_store_error(
        self, tmp_path: Path
    ) -> None:
        """A missing file cannot be re-moded."""
        with pytest.raises(StoreError):
            FilesystemBinaryStore(tmp_path / "missing").make_executable()

    def test_discard_removes_the_file_and_tolerates_absence(
        self, tmp_path: Path
    ) -> None:
        """Removing twice is fine; the second finds nothing."""
        target = tmp_path / "makeutil"
        target.write_bytes(_DATA)
        store = FilesystemBinaryStore(target)

        store.discard()
        store.discard()

        assert not target.exists()
