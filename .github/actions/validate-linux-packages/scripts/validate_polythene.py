"""Polythene sandbox helpers for package validation."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from plumbum import local
from plumbum.commands.processes import ProcessExecutionError

from script_utils import ensure_directory

from validate_commands import SIBLING_SCRIPTS, run_text
from validate_exceptions import ValidationError

__all__ = [
    "PolytheneSession",
    "default_polythene_path",
    "polythene_rootfs",
]


@dataclass(slots=True)
class PolytheneSession:
    """Handle for executing commands inside an exported polythene rootfs."""

    script: Path
    uid: str
    store: Path
    timeout: int | None = None

    @property
    def root(self) -> Path:
        """Return the root filesystem path for this session."""

        return self.store / self.uid

    def exec(self, *args: str, timeout: int | None = None) -> str:
        """Execute ``args`` inside the sandbox and return its stdout."""

        effective_timeout = timeout if timeout is not None else self.timeout
        cmd = local[
            "uv"
        ][
            "run",
            self.script.as_posix(),
            "exec",
            self.uid,
            "--store",
            self.store.as_posix(),
            "--",
            *args,
        ]
        return run_text(cmd, timeout=effective_timeout)


def default_polythene_path() -> Path:
    """Return the default path to the polythene helper script."""

    return SIBLING_SCRIPTS / "polythene.py"


def polythene_rootfs(
    polythene: Path,
    image: str,
    store: Path,
    *,
    timeout: int | None = None,
) -> Iterator[PolytheneSession]:
    """Yield a :class:`PolytheneSession` for ``image`` using ``store``."""

    ensure_directory(store)
    pull_cmd = local[
        "uv"
    ][
        "run",
        polythene.as_posix(),
        "pull",
        image,
        "--store",
        store.as_posix(),
    ]
    try:
        pull_output = run_text(pull_cmd, timeout=timeout)
    except ProcessExecutionError as exc:  # pragma: no cover - exercised in CI
        raise ValidationError(f"polythene pull failed: {exc}") from exc
    uid = pull_output.splitlines()[-1].strip()
    if not uid:
        raise ValidationError("polythene pull returned an empty identifier")
    session = PolytheneSession(polythene, uid, store, timeout)
    ensure_directory(session.root, exist_ok=True)
    try:
        session.exec("true")
    except ProcessExecutionError as exc:  # pragma: no cover - exercised in CI
        raise ValidationError(f"polythene exec failed: {exc}") from exc
    try:
        yield session
    finally:
        with contextlib.suppress(ProcessExecutionError):
            local[
                "uv"
            ][
                "run",
                polythene.as_posix(),
                "rm",
                uid,
                "--store",
                store.as_posix(),
            ]()
