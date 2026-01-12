#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["plumbum", "typer"]
# ///
"""Install cargo-nextest via cargo-binstall and verify its checksum."""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
from pathlib import Path

import typer
from cmd_utils_loader import run_cmd
from plumbum.cmd import cargo
from plumbum.commands.processes import ProcessExecutionError

# Keep CARGO_NEXTEST_VERSION and CARGO_NEXTEST_SHA256 in sync; update together.
CARGO_NEXTEST_VERSION = "0.9.120"
CARGO_NEXTEST_SHA256 = {
    "linux-x86_64": "8d717594668f0ec817405b9526cb657ca40fc888068277004860d0f253837d14",
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
    name = machine.lower()
    if name in {"x86_64", "amd64"}:
        return "x86_64"
    if name in {"arm64", "aarch64"}:
        return "aarch64"
    return name


def _platform_key() -> str:
    system = platform.system()
    machine = _normalize_machine(platform.machine())
    if system == "Linux":
        return f"linux-{machine}"
    if system == "Darwin":
        return "mac-universal"
    if system == "Windows":
        return f"windows-{machine}"
    return f"{system.lower()}-{machine}"


def _expected_sha_for_platform() -> str:
    key = _platform_key()
    try:
        return CARGO_NEXTEST_SHA256[key]
    except KeyError as exc:
        typer.echo(f"Unsupported platform for cargo-nextest: {key}", err=True)
        raise typer.Exit(1) from exc


def _sha256_path(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _find_nextest_binary() -> Path:
    resolved = shutil.which("cargo-nextest")
    if resolved:
        return Path(resolved)
    suffix = ".exe" if os.name == "nt" else ""
    candidate = Path.home() / ".cargo" / "bin" / f"cargo-nextest{suffix}"
    if candidate.is_file():
        return candidate
    typer.echo("cargo-nextest not found after installation", err=True)
    raise typer.Exit(1)


def _find_existing_nextest_binary() -> Path | None:
    resolved = shutil.which("cargo-nextest")
    if resolved:
        return Path(resolved)
    suffix = ".exe" if os.name == "nt" else ""
    candidate = Path.home() / ".cargo" / "bin" / f"cargo-nextest{suffix}"
    return candidate if candidate.is_file() else None


def install_cargo_nextest() -> None:
    """Install cargo-nextest using cargo-binstall."""
    try:
        cmd = cargo[
            "binstall",
            "cargo-nextest",
            "--version",
            CARGO_NEXTEST_VERSION,
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


def verify_nextest_binary(path: Path, expected_sha: str) -> None:
    """Verify the cargo-nextest binary against the expected SHA-256."""
    actual_sha = _sha256_path(path)
    if actual_sha != expected_sha:
        typer.echo(
            "cargo-nextest checksum mismatch: "
            f"expected {expected_sha}, got {actual_sha}",
            err=True,
        )
        raise typer.Exit(1)


def main() -> None:
    """Install cargo-nextest and verify the binary checksum."""
    expected_sha = _expected_sha_for_platform()
    existing = _find_existing_nextest_binary()
    if existing is not None:
        try:
            verify_nextest_binary(existing, expected_sha)
        except typer.Exit:
            pass
        else:
            typer.echo("cargo-nextest already installed and verified")
            return

    install_cargo_nextest()
    binary_path = _find_nextest_binary()
    verify_nextest_binary(binary_path, expected_sha)
    typer.echo("cargo-nextest installed and verified")


if __name__ == "__main__":
    typer.run(main)
