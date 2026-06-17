#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["plumbum", "typer"]
# ///
"""Install cargo-nextest via cargo-binstall and verify its checksum.

This script is executed by the ``generate-coverage`` action before Rust coverage
steps. It is responsible for selecting the correct checksum key for the current
runner platform, invoking ``cargo binstall`` when needed, and guarding against
binary replacement by verifying the SHA-256 digest.
"""

from __future__ import annotations

import ctypes
import hashlib
import logging
import os
import platform
import shutil
import typing as typ
from pathlib import Path

import typer
from cmd_utils_loader import run_cmd
from plumbum.cmd import cargo
from plumbum.commands.processes import ProcessExecutionError

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s %(message)s")

# Keep CARGO_NEXTEST_VERSION and CARGO_NEXTEST_SHA256 in sync; update together.
CARGO_NEXTEST_VERSION = "0.9.120"
CARGO_NEXTEST_SHA256 = {
    "linux-x86_64-gnu": (
        "8d717594668f0ec817405b9526cb657ca40fc888068277004860d0f253837d14"
    ),
    "linux-x86_64-musl": (
        "b05373ac79d5a1e200627ffd780c9cec96d7547311ac585d6c277d6394c2cd28"
    ),
    "linux-aarch64-gnu": (
        "901f10642066a848d4bc4eaee3d91642ad0476bea4a5de26832e838e4c32939e"
    ),
    "mac-universal": "d9f8aa57f88ea948ee68629cfc22a0a86ccd0d0143139983753dcb5f167085b8",
    "windows-x86_64": (
        "8e4160a8d710e753fd21a725e1771d20d948dbfa5d3472b57ee331f16c237af4"
    ),
    "windows-aarch64": (
        "9a1756ef23dff328f25ebf21c10be5dac7907e111782db63519474ec397f665c"
    ),
}


def _is_musl(
    *,
    library_name: str = "libc.so.6",
    ctypes_cdll: typ.Callable[[str], typ.Any] = ctypes.CDLL,
) -> bool:
    """Return ``True`` when the libc runtime appears to be musl.

    Parameters
    ----------
    library_name : str
        The libc shared-object name to probe with ``ctypes.CDLL``.
    ctypes_cdll : Callable[[str], object]
        Injectable dependency used only for tests.

    Returns
    -------
    bool
        ``True`` when musl is detected, ``False`` for GNU libc.

    Raises
    ------
    OSError
        Propagated when the libc probe fails before symbol resolution.
    """
    try:
        libc = ctypes_cdll(library_name)
    except OSError:
        logger.exception(
            "Failed to load libc for libc-family detection using %s",
            library_name,
        )
        raise

    try:
        version_fn = libc.gnu_get_libc_version
    except AttributeError:
        logger.info("Detected musl libc (missing gnu_get_libc_version symbol)")
        return True

    version = version_fn()
    if hasattr(version, "decode"):
        version = version.decode()
    logger.debug("Detected GNU libc version marker %s", version)
    return False


def _normalize_machine(machine: str) -> str:
    """Normalise runner architecture labels for checksum lookup keys."""
    name = machine.lower()
    if name in {"x86_64", "amd64"}:
        return "x86_64"
    if name in {"arm64", "aarch64"}:
        return "aarch64"
    return name


def _platform_key() -> str:
    """Return the platform key used to resolve the expected checksum."""
    system = platform.system()
    machine = _normalize_machine(platform.machine())
    if system == "Linux":
        libc = "musl" if _is_musl() else "gnu"
        key = f"linux-{machine}-{libc}"
        logger.info("Selecting libc-aware platform key: %s", key)
        return key
    if system == "Darwin":
        key = "mac-universal"
    elif system == "Windows":
        key = f"windows-{machine}"
    else:
        key = f"{system.lower()}-{machine}"

    logger.info("Selecting platform key: %s", key)
    return key


def _report_platform_key(key: str) -> None:
    """Log the normalized checksum lookup key."""
    logger.info("Resolved cargo-nextest platform key: %s", key)


def _binstall_target_for_key(key: str) -> str | None:
    """Return the explicit binstall ``--targets`` triple for *key*, or None.

    Returns a non-``None`` value only for Linux x86_64 variants where binstall
    cannot reliably auto-select the correct binary, to ensure it downloads the
    variant whose SHA is stored in :data:`CARGO_NEXTEST_SHA256`.
    """
    return {
        "linux-x86_64-gnu": "x86_64-unknown-linux-gnu",
        "linux-x86_64-musl": "x86_64-unknown-linux-musl",
    }.get(key)


def _expected_sha_for_platform() -> tuple[str, str | None]:
    """Return the pinned SHA-256 and optional binstall target for this platform."""
    key = _platform_key()
    _report_platform_key(key)
    try:
        sha = CARGO_NEXTEST_SHA256[key]
    except KeyError as exc:
        typer.echo(f"Unsupported platform for cargo-nextest: {key}", err=True)
        raise typer.Exit(1) from exc
    return sha, _binstall_target_for_key(key)


def _sha256_path(path: Path) -> str:
    """Compute the SHA-256 digest for ``path``."""
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _resolve_nextest_binary() -> Path | None:
    """Return a resolved ``cargo-nextest`` executable if one exists."""
    resolved = shutil.which("cargo-nextest")
    if resolved:
        return Path(resolved)
    suffix = ".exe" if os.name == "nt" else ""
    candidate = Path.home() / ".cargo" / "bin" / f"cargo-nextest{suffix}"
    return candidate if candidate.is_file() else None


def _find_nextest_binary() -> Path:
    """Resolve the installed ``cargo-nextest`` or exit with code 1."""
    resolved = _resolve_nextest_binary()
    if resolved is not None:
        return resolved
    typer.echo("cargo-nextest not found after installation", err=True)
    logger.error("cargo-nextest binary could not be located after installation")
    raise typer.Exit(1)


def install_cargo_nextest(target: str | None = None) -> None:
    """Install cargo-nextest using cargo-binstall.

    When *target* is supplied the ``--targets`` flag is forwarded to
    ``cargo binstall`` so it downloads the exact binary variant whose checksum
    is stored in :data:`CARGO_NEXTEST_SHA256`, overriding binstall's own
    platform heuristics.
    """
    try:
        args: list[str] = [
            "binstall",
            "cargo-nextest",
            "--version",
            CARGO_NEXTEST_VERSION,
            "--locked",
            "--no-confirm",
            "--force",
        ]
        if target is not None:
            logger.info("Passing cargo-binstall target override: %s", target)
            args += ["--targets", target]
        run_cmd(cargo[args])
        typer.echo("cargo-nextest installed successfully")
    except ProcessExecutionError as exc:
        typer.echo(
            f"cargo binstall failed with code {exc.retcode}: {exc.stderr}",
            err=True,
        )
        raise typer.Exit(code=exc.retcode or 1) from exc


def verify_nextest_binary(path: Path, expected_sha: str) -> bool:
    """Verify the cargo-nextest binary against the expected SHA-256."""
    actual_sha = _sha256_path(path)
    if actual_sha != expected_sha:
        logger.error(
            "cargo-nextest checksum mismatch for %s: expected %s, got %s",
            path,
            expected_sha,
            actual_sha,
        )
        typer.echo(
            "cargo-nextest checksum mismatch: "
            f"expected {expected_sha}, got {actual_sha}",
            err=True,
        )
        return False
    return True


def main() -> None:
    """Install cargo-nextest and verify the binary checksum."""
    expected_sha, target = _expected_sha_for_platform()
    existing = _resolve_nextest_binary()
    if existing is not None and verify_nextest_binary(existing, expected_sha):
        logger.info("Using preinstalled cargo-nextest at %s", existing)
        typer.echo("cargo-nextest already installed and verified")
        return

    install_cargo_nextest(target)
    binary_path = _find_nextest_binary()
    if not verify_nextest_binary(binary_path, expected_sha):
        raise typer.Exit(1)
    logger.info("cargo-nextest installation and verification succeeded")
    typer.echo("cargo-nextest installed and verified")


if __name__ == "__main__":
    typer.run(main)
