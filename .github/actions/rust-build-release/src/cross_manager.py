"""Utilities for ensuring the `cross` tool is available."""

from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import typer
from packaging import version as pkg_version
from plumbum import local
from plumbum.commands.processes import ProcessExecutionError
from utils import (
    UnexpectedExecutableError,
    ensure_allowed_executable,
    run_validated,
)

from cmd_utils import run_cmd

_NON_HTTPS_ERROR = "non-HTTPS URL"
_EMPTY_HASH_ERROR = "empty hash file"
_MISSING_HASH_ENTRY_ERROR = "missing hash entry"


def _download_https(url: str, destination: Path) -> None:
    """Download *url* to *destination*, enforcing HTTPS transport."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(_NON_HTTPS_ERROR)
    with urllib.request.urlopen(url) as response:  # noqa: S310
        destination.write_bytes(response.read())


def _read_sha256(path: Path) -> str:
    """Return the first token in *path* interpreted as a SHA-256 digest."""
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        raise ValueError(_EMPTY_HASH_ERROR)
    token = content.split()[0]
    if not token:
        raise ValueError(_MISSING_HASH_ENTRY_ERROR)
    return token


def _compute_sha256(path: Path) -> str:
    """Compute the SHA-256 digest of *path*."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_member(archive_path: Path, suffix: str, destination: Path) -> Path:
    """Extract the first archive member ending with *suffix* into *destination*."""
    with zipfile.ZipFile(archive_path) as archive:
        member = next(
            (name for name in archive.namelist() if name.lower().endswith(suffix)),
            None,
        )
        if member is None:
            raise FileNotFoundError(suffix)
        archive.extract(member, destination)
        return destination / member


def install_cross_release(required_version: str) -> bool:
    """Install cross from a prebuilt Windows binary release."""
    asset = "cross-x86_64-pc-windows-msvc.zip"
    url = (
        "https://github.com/cross-rs/cross/releases/download/"
        f"v{required_version}/{asset}"
    )
    hash_url = f"{url}.sha256"
    typer.echo(f"Downloading cross {required_version} binary for Windows...")
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            archive_path = Path(tmpdir) / asset
            hash_path = Path(tmpdir) / f"{asset}.sha256"
            try:
                _download_https(url, archive_path)
                _download_https(hash_url, hash_path)
            except ValueError:
                typer.echo(
                    "::warning:: refusing to download cross from non-HTTPS URL",
                    err=True,
                )
                return False

            try:
                expected_hash = _read_sha256(hash_path)
            except ValueError:
                typer.echo(
                    "::warning:: missing SHA256 hash for cross release archive",
                    err=True,
                )
                return False

            actual_hash = _compute_sha256(archive_path)
            if actual_hash.lower() != expected_hash.lower():
                typer.echo(
                    "::warning:: downloaded cross archive hash mismatch",
                    err=True,
                )
                return False

            try:
                extracted = _extract_member(archive_path, "cross.exe", Path(tmpdir))
            except FileNotFoundError:
                typer.echo(
                    "::warning:: cross.exe not found in release archive",
                    err=True,
                )
                return False

            destination_dir = Path.home() / ".cargo" / "bin"
            destination_dir.mkdir(parents=True, exist_ok=True)
            destination = destination_dir / "cross.exe"
            if destination.exists():
                destination.unlink()
            shutil.move(str(extracted), destination)
            try:
                cross_exec = ensure_allowed_executable(
                    destination, ("cross", "cross.exe")
                )
                result = run_validated(
                    cross_exec,
                    ["--version"],
                    allowed_names=("cross", "cross.exe"),
                )
            except (OSError, ProcessExecutionError) as exc:
                typer.echo(
                    f"::warning:: installed cross failed to execute: {exc}",
                    err=True,
                )
                return False
            if version_output := result.stdout.strip():
                typer.echo(f"Installed cross binary reports: {version_output}")
    except urllib.error.URLError as exc:  # pragma: no cover - network failure
        typer.echo(
            "::warning:: failed to download cross binary from GitHub releases: "
            f"{exc.reason}",
            err=True,
        )
    except (OSError, zipfile.BadZipFile) as exc:
        typer.echo(
            f"::warning:: failed to install cross binary: {exc}",
            err=True,
        )
    else:
        return True
    return False


def ensure_cross(required_cross_version: str) -> tuple[str | None, str | None]:
    """Ensure cross is installed and up to date."""

    def get_cross_version(path: str) -> str | None:
        try:
            cross_exec = ensure_allowed_executable(path, ("cross", "cross.exe"))
            result = run_validated(
                cross_exec,
                ["--version"],
                allowed_names=("cross", "cross.exe"),
            )
            version_line = result.stdout.strip().split("\n")[0]
            if version_line.startswith("cross "):
                return version_line.split(" ")[1]
        except (
            OSError,
            ProcessExecutionError,
            UnexpectedExecutableError,
        ):
            return None

    def version_compare(installed: str, required: str) -> bool:
        return pkg_version.parse(installed) >= pkg_version.parse(required)

    cross_path = shutil.which("cross")
    cross_version = get_cross_version(cross_path) if cross_path else None

    needs_install = cross_path is None or not version_compare(
        cross_version or "0", required_cross_version
    )

    if needs_install:
        if cross_path is None:
            typer.echo("Installing cross (not found)...")
        else:
            typer.echo(
                "Upgrading cross (found version "
                f"{cross_version}, required >= {required_cross_version})..."
            )
        installed = False
        if sys.platform == "win32":
            installed = install_cross_release(required_cross_version)
        if not installed:
            try:
                run_cmd(
                    local["cargo"][
                        "install",
                        "--locked",
                        "cross",
                        "--version",
                        required_cross_version,
                    ]
                )
            except ProcessExecutionError:
                try:
                    run_cmd(
                        local["cargo"][
                            "install",
                            "--locked",
                            "cross",
                            "--git",
                            "https://github.com/cross-rs/cross",
                            "--tag",
                            f"v{required_cross_version}",
                        ]
                    )
                except ProcessExecutionError:
                    if sys.platform == "win32":
                        typer.echo(
                            "::warning:: cross install failed; continuing without "
                            "cross",
                            err=True,
                        )
                        return None, None
                    raise
    else:
        typer.echo(f"Using cached cross ({cross_version})")

    cross_path = shutil.which("cross")
    cross_version = get_cross_version(cross_path) if cross_path else None
    return cross_path, cross_version
