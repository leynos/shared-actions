"""Serialize Skylos whitelist updates across supported developer platforms."""

from __future__ import annotations

import argparse
import errno
import os
import re
import sys
import time
import typing as typ
from contextlib import contextmanager
from pathlib import Path

from plumbum import RETCODE, local
from plumbum.commands.processes import CommandNotFound

if typ.TYPE_CHECKING:
    import collections.abc as cabc

_ENVIRONMENT_ASSIGNMENT: typ.Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*=.*"
)


@contextmanager
def _exclusive_windows_file_lock(lock_file: typ.BinaryIO) -> cabc.Iterator[None]:
    """Hold an exclusive Windows byte-range lock on ``lock_file``."""
    import msvcrt

    lock_file.seek(0, os.SEEK_END)
    if lock_file.tell() == 0:
        lock_file.write(b"\0")
        lock_file.flush()
    lock_file.seek(0)
    _acquire_windows_lock(lock_file)
    try:
        yield
    finally:
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)


def _try_acquire_windows_lock(lock_file: typ.BinaryIO) -> bool:
    """Try one non-blocking Windows lock acquisition."""
    import msvcrt

    try:
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError as error:
        retryable_errors = {
            errno.EACCES,
            errno.EAGAIN,
            getattr(errno, "EDEADLK", errno.EACCES),
        }
        if error.errno not in retryable_errors:
            raise
        return False
    return True


def _acquire_windows_lock(lock_file: typ.BinaryIO) -> None:
    """Wait until the Windows byte-range lock is acquired."""
    while not _try_acquire_windows_lock(lock_file):
        time.sleep(0.05)


@contextmanager
def _exclusive_posix_file_lock(lock_file: typ.BinaryIO) -> cabc.Iterator[None]:
    """Hold an exclusive POSIX flock on ``lock_file``."""
    import fcntl

    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
    try:
        yield
    finally:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@contextmanager
def _exclusive_file_lock(lock_file: typ.BinaryIO) -> cabc.Iterator[None]:
    """Hold an exclusive advisory lock on ``lock_file``."""
    platform_lock = (
        _exclusive_windows_file_lock if os.name == "nt" else _exclusive_posix_file_lock
    )
    with platform_lock(lock_file):
        yield


def _child_environment(command: list[str]) -> tuple[dict[str, str], list[str]]:
    """Apply leading Make-style environment assignments to a child command."""
    environment = os.environ.copy()
    while command and _ENVIRONMENT_ASSIGNMENT.fullmatch(command[0]):
        name, value = command.pop(0).split("=", maxsplit=1)
        environment[name] = value
    return environment, command


def main(arguments: list[str] | None = None) -> int:
    """Run a Skylos whitelist update while holding its cross-platform lock.

    Parameters
    ----------
    arguments : list[str] or None, optional
        Command-line arguments. When omitted, use ``sys.argv[1:]``.

    Returns
    -------
    int
        The Skylos command's exit status, or 127 when it cannot be executed.
    """
    raw_arguments = list(sys.argv[1:] if arguments is None else arguments)
    try:
        separator = raw_arguments.index("--")
    except ValueError:
        print("skylos-allow: expected -- before the Skylos command", file=sys.stderr)
        return 2

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock-file", required=True, type=Path)
    options = parser.parse_args(raw_arguments[:separator])
    command = raw_arguments[separator + 1 :]
    environment, command = _child_environment(command)
    if not command:
        parser.error("the Skylos command after -- must not be empty")

    lock_path: Path = options.lock_file
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_file, _exclusive_file_lock(lock_file):
        try:
            # The command is the explicit Skylos CLI supplied by the Makefile.
            child = local[command[0]][command[1:]].with_env(**environment)
            return_code = child & RETCODE(FG=True)
        except (CommandNotFound, OSError) as error:
            print(
                f"skylos-allow: cannot execute {command[0]!r}: {error}",
                file=sys.stderr,
            )
            return 127
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
