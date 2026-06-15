#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["plumbum", "typer"]
# ///
"""Install and verify cargo-nextest for generate-coverage GitHub Actions runs.

The generate-coverage pipeline calls this helper before Rust coverage when
`use-cargo-nextest` is enabled so `cargo llvm-cov nextest` can run with a
known-good, pinned toolchain binary. The installed binary is validated with a
platform-specific SHA-256 checksum before the action continues.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import platform
import shutil
import typing as typ
from pathlib import Path

import typer
from cmd_utils_loader import run_cmd
from plumbum.cmd import cargo
from plumbum.commands.processes import ProcessExecutionError

if typ.TYPE_CHECKING:
    import collections.abc as cabc

# Keep CARGO_NEXTEST_VERSION and CARGO_NEXTEST_SHA256 in sync; update together.
# linux-x86_64-gnu  : SHA of the extracted binary from the
#   -x86_64-unknown-linux-gnu.tar.gz release
# linux-x86_64-musl : SHA of the extracted binary from the
#   -x86_64-unknown-linux-musl.tar.gz release
CARGO_NEXTEST_VERSION = "0.9.120"
CARGO_NEXTEST_SHA256 = {
    "linux-x86_64-gnu": (
        "73c7eb58e6507c10821998de343586cdcd37f99129f69dd0ec5605fd6d7eb291"
    ),
    "linux-x86_64-musl": (
        "8d717594668f0ec817405b9526cb657ca40fc888068277004860d0f253837d14"
    ),
    "linux-aarch64": "901f10642066a848d4bc4eaee3d91642ad0476bea4a5de26832e838e4c32939e",
    "mac-universal": "d9f8aa57f88ea948ee68629cfc22a0a86ccd0d0143139983753dcb5f167085b8",
    "windows-x86_64": (
        "8e4160a8d710e753fd21a725e1771d20d948dbfa5d3472b57ee331f16c237af4"
    ),
    "windows-aarch64": (
        "9a1756ef23dff328f25ebf21c10be5dac7907e111782db63519474ec397f665c"
    ),
}


def _normalize_machine(machine: str) -> str:
    """Normalize platform machine names to cargo-nextest release keys."""
    name = machine.lower()
    if name in {"x86_64", "amd64"}:
        return "x86_64"
    if name in {"arm64", "aarch64"}:
        return "aarch64"
    return name


def _probe_libc_is_musl(cdll: cabc.Callable[[typ.Any], typ.Any]) -> bool:
    """Return True when probing libc finds musl or cannot find glibc symbols."""
    try:
        libc = cdll(None)
        # glibc exposes gnu_get_libc_version(); musl does not.
        libc.gnu_get_libc_version.restype = ctypes.c_char_p
        libc.gnu_get_libc_version()
    except (OSError, AttributeError):
        return True
    else:
        return False


def _is_musl() -> bool:
    """Return True when the running libc is musl rather than glibc."""
    is_musl = _probe_libc_is_musl(ctypes.CDLL)
    typer.echo(f"Detected libc for cargo-nextest: {'musl' if is_musl else 'glibc'}")
    return is_musl


def _platform_key() -> str:
    """Return the cargo-nextest checksum key for the current platform."""
    system = platform.system()
    machine = _normalize_machine(platform.machine())
    if system == "Linux":
        if machine == "x86_64":
            suffix = "musl" if _is_musl() else "gnu"
            key = f"linux-x86_64-{suffix}"
            typer.echo(f"Selected cargo-nextest platform key: {key}")
            return key
        return f"linux-{machine}"
    if system == "Darwin":
        return "mac-universal"
    if system == "Windows":
        return f"windows-{machine}"
    return f"{system.lower()}-{machine}"


def _expected_sha_for_platform() -> str:
    """Return the pinned cargo-nextest checksum for the current platform."""
    key = _platform_key()
    try:
        return CARGO_NEXTEST_SHA256[key]
    except KeyError as exc:
        typer.echo(f"Unsupported platform for cargo-nextest: {key}", err=True)
        raise typer.Exit(1) from exc


def _sha256_path(path: Path) -> str:
    """Return the SHA-256 digest for a file path."""
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _resolve_nextest_binary() -> Path | None:
    """Return the cargo-nextest binary path when it already exists."""
    resolved = shutil.which("cargo-nextest")
    if resolved:
        return Path(resolved)
    suffix = ".exe" if os.name == "nt" else ""
    candidate = Path.home() / ".cargo" / "bin" / f"cargo-nextest{suffix}"
    return candidate if candidate.is_file() else None


def _find_nextest_binary() -> Path:
    """Return the installed cargo-nextest binary path or exit with an error."""
    resolved = _resolve_nextest_binary()
    if resolved is not None:
        return resolved
    typer.echo("cargo-nextest not found after installation", err=True)
    raise typer.Exit(1)


def install_cargo_nextest() -> None:
    """Install cargo-nextest using cargo-binstall."""
    try:
        cmd = cargo[
            "binstall",
            "cargo-nextest",
            "--version",
            CARGO_NEXTEST_VERSION,
            "--locked",
            "--no-confirm",
            "--force",
        ]
        run_cmd(cmd)
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
        typer.echo(
            "cargo-nextest checksum mismatch: "
            f"expected {expected_sha}, got {actual_sha}",
            err=True,
        )
        return False
    return True


def main() -> None:
    """Install cargo-nextest and verify the binary checksum."""
    expected_sha = _expected_sha_for_platform()
    existing = _resolve_nextest_binary()
    if existing is not None and verify_nextest_binary(existing, expected_sha):
        typer.echo("cargo-nextest already installed and verified")
        return

    install_cargo_nextest()
    binary_path = _find_nextest_binary()
    if not verify_nextest_binary(binary_path, expected_sha):
        raise typer.Exit(1)
    typer.echo("cargo-nextest installed and verified")


if __name__ == "__main__":
    typer.run(main)
