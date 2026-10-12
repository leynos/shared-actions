"""Unit contracts for the portable whitelist lock.

The POSIX lock is ``fcntl.flock`` and needs no stand-in, but the Windows
byte-range lock and the platform dispatch that selects it cannot run on
the Linux gate, so they are driven here against a stub ``msvcrt``.
"""

from __future__ import annotations

import errno
import sys
import types
import typing as typ
from contextlib import nullcontext

import pytest

from workflow_scripts import skylos_allow
from workflow_scripts.tests.skylos_contract_support import (
    _WINDOWS_LOCK_NONBLOCKING,
    _WINDOWS_LOCK_UNLOCK,
)

if typ.TYPE_CHECKING:
    from pathlib import Path


class TestSkylosLockContract:
    """The lock must retry contention on Windows and dispatch by platform."""

    def test_windows_file_lock_retries_and_releases(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The Windows byte-range lock retries contention and unlocks on exit."""
        calls: list[int] = []
        attempts = 0

        def locking(_file_descriptor: int, mode: int, byte_count: int) -> None:
            nonlocal attempts
            assert byte_count == 1, "Windows Skylos locks must cover exactly one byte"
            calls.append(mode)
            if mode == _WINDOWS_LOCK_NONBLOCKING:
                attempts += 1
                if attempts == 1:
                    raise OSError(errno.EACCES, "lock is held")

        fake_msvcrt = types.ModuleType("msvcrt")
        fake_msvcrt.__dict__.update(
            LK_NBLCK=_WINDOWS_LOCK_NONBLOCKING,
            LK_UNLCK=_WINDOWS_LOCK_UNLOCK,
            locking=locking,
        )
        monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
        monkeypatch.setattr(
            skylos_allow, "time", types.SimpleNamespace(sleep=lambda _delay: None)
        )
        lock_path = tmp_path / "skylos-whitelist.lock"
        with (
            lock_path.open("a+b") as lock_file,
            skylos_allow._exclusive_windows_file_lock(lock_file),
        ):
            assert lock_path.stat().st_size == 1, (
                "Windows byte-range locks need a persistent lock byte"
            )

        assert calls == [
            _WINDOWS_LOCK_NONBLOCKING,
            _WINDOWS_LOCK_NONBLOCKING,
            _WINDOWS_LOCK_UNLOCK,
        ], "Windows Skylos locks must retry contention before unlocking"
        assert lock_path.read_bytes() == b"\0", (
            "releasing a Windows lock must leave its coordination byte intact"
        )

    def test_windows_file_lock_reraises_non_contention_errors(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only Windows lock-contention errors may be retried."""

        def locking(_file_descriptor: int, _mode: int, _byte_count: int) -> None:
            raise OSError(errno.EBADF, "invalid lock descriptor")

        fake_msvcrt = types.ModuleType("msvcrt")
        fake_msvcrt.__dict__.update(
            LK_NBLCK=_WINDOWS_LOCK_NONBLOCKING,
            locking=locking,
        )
        monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
        lock_path = tmp_path / "skylos-whitelist.lock"
        with (
            lock_path.open("a+b") as lock_file,
            pytest.raises(OSError, match="invalid lock descriptor"),
        ):
            skylos_allow._try_acquire_windows_lock(lock_file)

    def test_file_lock_dispatches_to_windows_strategy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Platform dispatch must select the Windows lock without host flock."""
        sentinel = typ.cast("typ.BinaryIO", object())
        selected: list[typ.BinaryIO] = []

        def windows_lock(lock_file: typ.BinaryIO) -> nullcontext[None]:
            selected.append(lock_file)
            return nullcontext()

        monkeypatch.setattr(skylos_allow, "os", types.SimpleNamespace(name="nt"))
        monkeypatch.setattr(skylos_allow, "_exclusive_windows_file_lock", windows_lock)

        with skylos_allow._exclusive_file_lock(sentinel):
            pass
        assert selected == [sentinel], (
            "Windows lock dispatch must select the Windows advisory lock"
        )
