"""The installed-binary store: the one place the install path touches disk.

`install_makeutil` decides policy - reuse a cached binary or download, which
digests must agree - and asks a `BinaryStore` to do every filesystem effect. The
production `FilesystemBinaryStore` maps each `OSError` to a `StoreError`, so
storage failure is an explicit, typed outcome of the port rather than an
exception the policy must anticipate. Tests drive the policy with an
in-memory store and this adapter with real temporary directories.
"""

from __future__ import annotations

import contextlib
import dataclasses as dc
import hashlib
import os
import tempfile
import typing as typ
from pathlib import Path

from makeutil_errors import StoreError

_EXECUTABLE_MODE = 0o755


class BinaryStore(typ.Protocol):
    """Where the verified binary lives, and every effect on it."""

    @property
    def location(self) -> Path:
        """Return where the binary is (or will be) installed."""
        ...

    def digest(self) -> str | None:
        """Return the SHA-256 of the stored binary, or `None` when absent."""
        ...

    def make_executable(self) -> None:
        """Give the stored binary the executable bit."""
        ...

    def install(self, data: bytes) -> None:
        """Atomically replace the stored binary with `data`."""
        ...

    def discard(self) -> None:
        """Remove the stored binary if there is one."""
        ...


@dc.dataclass(slots=True, frozen=True)
class FilesystemBinaryStore:
    """A `BinaryStore` backed by one file on the local filesystem.

    Every method raises `StoreError` rather than `OSError`.
    """

    path: Path

    @property
    def location(self) -> Path:
        """Return the path the binary is installed at."""
        return self.path

    def digest(self) -> str | None:
        """Return the stored file's SHA-256, or `None` when it is not a file."""
        try:
            if not self.path.is_file():
                return None
            return hashlib.sha256(self.path.read_bytes()).hexdigest()
        except OSError as error:
            msg = f"could not read the cached binary: {error}"
            raise StoreError(msg) from error

    def make_executable(self) -> None:
        """Set the executable bit, which a cache restore does not preserve."""
        try:
            self.path.chmod(_EXECUTABLE_MODE)
        except OSError as error:
            msg = f"could not make the cached binary executable: {error}"
            raise StoreError(msg) from error

    def install(self, data: bytes) -> None:
        """Write `data` to a staged file and move it into place atomically.

        The staged file is created beside the target, given the executable
        bit, and moved into place with `Path.replace`, which is atomic on the
        same filesystem. A failure at any point removes the staged file rather
        than leaving a partial one where a caller might find it.
        """
        staged: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, staged_name = tempfile.mkstemp(
                dir=self.path.parent, prefix=f".{self.path.name}."
            )
            staged = Path(staged_name)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
            staged.chmod(_EXECUTABLE_MODE)
            staged.replace(self.path)
        except OSError as error:
            if staged is not None:
                with contextlib.suppress(OSError):
                    staged.unlink(missing_ok=True)
            msg = f"could not install the binary: {error}"
            raise StoreError(msg) from error

    def discard(self) -> None:
        """Remove the stored binary; absent is not an error."""
        try:
            self.path.unlink(missing_ok=True)
        except OSError as error:
            msg = f"could not remove the binary: {error}"
            raise StoreError(msg) from error
