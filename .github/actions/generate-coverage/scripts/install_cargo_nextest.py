#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["typer"]
# ///
"""Install cargo-nextest from its official release and verify its checksums.

This script selects the official archive for the runner, verifies both the
archive and extracted binary, and installs it into ``CARGO_HOME/bin``. It never
invokes Cargo, so a missing prebuilt binary is a hard error rather than a
source-build fallback.
"""

from __future__ import annotations

import ctypes
import hashlib
import logging
import os
import platform
import shutil
import tarfile
import tempfile
import time
import typing as typ
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import typer

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s %(message)s")

# A cargo-nextest release archive is a few MB; 200 MB gives generous headroom
# for future growth while still bounding the disk a compromised or redirected
# endpoint could consume before the digest check ever gets to reject it. The
# digest protects integrity, not disk space, so this cap is enforced first.
_MAX_ARCHIVE_BYTES = 200 * 1024 * 1024

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


class ReleaseAsset(typ.NamedTuple):
    """Describe one pinned cargo-nextest release archive."""

    target: str
    extension: str
    sha256: str

    @property
    def filename(self) -> str:
        """Return the official archive filename."""
        return f"cargo-nextest-{CARGO_NEXTEST_VERSION}-{self.target}.{self.extension}"


CARGO_NEXTEST_RELEASE_ASSETS = {
    "linux-x86_64-gnu": ReleaseAsset(
        "x86_64-unknown-linux-gnu",
        "tar.gz",
        "a5b1c12500c47e27af4baf533c917bf1b38e9bf2e6ffb063dfa1de6e75aa8726",
    ),
    "linux-x86_64-musl": ReleaseAsset(
        "x86_64-unknown-linux-musl",
        "tar.gz",
        "e00511fc23241ffd3ca1d95b23bde8a9cd0fb96bb691a9957a909ba74e7a5238",
    ),
    "linux-aarch64-gnu": ReleaseAsset(
        "aarch64-unknown-linux-gnu",
        "tar.gz",
        "5e13751733a1fc4d26984ad5e1bce10d057d95299b02ed3ac96877b7288c8feb",
    ),
    "mac-universal": ReleaseAsset(
        "universal-apple-darwin",
        "tar.gz",
        "e2aa5a27bfdac66c913346985a1ceff50ab9590b846798440464410bd5a309b9",
    ),
    "windows-x86_64": ReleaseAsset(
        "x86_64-pc-windows-msvc",
        "zip",
        "ccb22cb26d6816eb39992f276c0f058ea9a5842ee35f70ee48a4ee84fd671538",
    ),
    "windows-aarch64": ReleaseAsset(
        "aarch64-pc-windows-msvc",
        "zip",
        "8b6475c9d6fd6946a8a8cced8213c1e5b1f9df219cab999831905187887003f9",
    ),
}


def emit_metric(line: str) -> None:
    """Append one bounded metric line to the job summary, if one is set.

    Does nothing when ``GITHUB_STEP_SUMMARY`` is unset, which is the case
    outside a GitHub Actions job (for example, when running this script or
    its tests locally).
    """
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with Path(summary_path).open("a", encoding="utf-8") as handle:
        handle.write(f"{line}\n")


def _is_musl(
    *,
    library_name: str = "libc.so.6",
    ctypes_cdll: typ.Callable[[str], typ.Any] = ctypes.CDLL,
) -> bool:
    """Return whether the libc runtime appears to be musl."""
    try:
        libc = ctypes_cdll(library_name)
    except OSError:
        logger.exception("Failed to load libc for detection using %s", library_name)
        raise

    try:
        version_fn = libc.gnu_get_libc_version
    except AttributeError:
        logger.info("Detected musl libc")
        return True

    version = version_fn()
    if hasattr(version, "decode"):
        version = version.decode()
    logger.debug("Detected GNU libc version marker %s", version)
    return False


def _normalize_machine(machine: str) -> str:
    """Normalize runner architecture labels for checksum lookup keys."""
    name = machine.lower()
    if name in {"x86_64", "amd64"}:
        return "x86_64"
    if name in {"arm64", "aarch64"}:
        return "aarch64"
    return name


def _platform_key() -> str:
    """Return the platform key used to resolve the pinned release."""
    system = platform.system()
    machine = _normalize_machine(platform.machine())
    if system == "Linux":
        libc = "musl" if _is_musl() else "gnu"
        key = f"linux-{machine}-{libc}"
    elif system == "Darwin":
        key = "mac-universal"
    elif system == "Windows":
        key = f"windows-{machine}"
    else:
        key = f"{system.lower()}-{machine}"
    logger.info("Resolved cargo-nextest platform key: %s", key)
    return key


def _release_for_platform() -> tuple[str, ReleaseAsset]:
    """Return the pinned binary digest and release archive for this platform."""
    key = _platform_key()
    try:
        return CARGO_NEXTEST_SHA256[key], CARGO_NEXTEST_RELEASE_ASSETS[key]
    except KeyError as exc:
        typer.echo(f"Unsupported platform for cargo-nextest: {key}", err=True)
        raise typer.Exit(1) from exc


def _sha256_path(path: Path) -> str:
    """Compute the SHA-256 digest for ``path``."""
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _cargo_bin() -> Path:
    """Return the Cargo binary directory honoured by the caller."""
    cargo_home = Path(os.environ.get("CARGO_HOME", Path.home() / ".cargo"))
    return cargo_home / "bin"


def _resolve_nextest_binary() -> Path | None:
    """Return a resolved ``cargo-nextest`` executable if one exists."""
    resolved = shutil.which("cargo-nextest")
    if resolved:
        return Path(resolved)
    suffix = ".exe" if os.name == "nt" else ""
    candidate = _cargo_bin() / f"cargo-nextest{suffix}"
    return candidate if candidate.is_file() else None


def _find_nextest_binary() -> Path:
    """Resolve the installed cargo-nextest executable or fail clearly."""
    resolved = _resolve_nextest_binary()
    if resolved is not None:
        return resolved
    typer.echo("cargo-nextest not found after installation", err=True)
    raise typer.Exit(1)


class _ArchiveTooLargeError(Exception):
    """Raised when a downloaded archive exceeds ``_MAX_ARCHIVE_BYTES``."""

    def __init__(self, bytes_read: int) -> None:
        super().__init__(
            f"archive exceeded {_MAX_ARCHIVE_BYTES} bytes (read {bytes_read})"
        )
        self.bytes_read = bytes_read


def _copy_bounded(
    source: typ.IO[bytes],
    destination: typ.IO[bytes],
    max_bytes: int,
    *,
    chunk_size: int = 1024 * 1024,
) -> int:
    """Copy ``source`` to ``destination`` in chunks, up to ``max_bytes``.

    Returns
    -------
    int
        The total number of bytes copied.

    Raises
    ------
    _ArchiveTooLargeError
        If more than ``max_bytes`` are read from ``source`` before it is
        exhausted.
    """
    total = 0
    while True:
        chunk = source.read(chunk_size)
        if not chunk:
            return total
        total += len(chunk)
        if total > max_bytes:
            raise _ArchiveTooLargeError(total)
        destination.write(chunk)


def _download_archive(asset: ReleaseAsset, destination: Path) -> None:
    """Download the pinned official release archive to ``destination``."""
    url = (
        "https://github.com/nextest-rs/nextest/releases/download/"
        f"cargo-nextest-{CARGO_NEXTEST_VERSION}/{asset.filename}"
    )
    # The URL is composed solely from pinned constants and an allow-listed
    # release asset, so non-HTTPS schemes cannot reach this boundary.
    request = urllib.request.Request(  # noqa: S310
        url,
        headers={"User-Agent": "generate-coverage"},
    )
    logger.info("event=nextest.download.start archive=%s url=%s", asset.filename, url)
    started = time.monotonic()
    try:
        with (
            urllib.request.urlopen(request, timeout=60) as response,  # noqa: S310
            destination.open("wb") as output,
        ):
            bytes_written = _copy_bounded(response, output, _MAX_ARCHIVE_BYTES)
    except _ArchiveTooLargeError as exc:
        destination.unlink(missing_ok=True)
        duration = time.monotonic() - started
        logger.error(  # noqa: TRY400 - the caller converts this to an exit code.
            "event=nextest.download.finish archive=%s outcome=too-large "
            "duration_seconds=%.3f bytes=%d max_bytes=%d",
            asset.filename,
            duration,
            exc.bytes_read,
            _MAX_ARCHIVE_BYTES,
        )
        emit_metric(
            f"cargo-nextest.download=failed duration_seconds={duration:.3f} bytes=0"
        )
        typer.echo(
            f"cargo-nextest release archive exceeded {_MAX_ARCHIVE_BYTES} bytes "
            "and was discarded",
            err=True,
        )
        raise typer.Exit(1) from exc
    except (OSError, urllib.error.URLError) as exc:
        duration = time.monotonic() - started
        logger.error(  # noqa: TRY400 - the caller converts this to an exit code.
            "event=nextest.download.finish archive=%s outcome=failed "
            "duration_seconds=%.3f error=%s",
            asset.filename,
            duration,
            exc,
        )
        emit_metric(
            f"cargo-nextest.download=failed duration_seconds={duration:.3f} bytes=0"
        )
        typer.echo(f"cargo-nextest release download failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    duration = time.monotonic() - started
    logger.info(
        "event=nextest.download.finish archive=%s outcome=ok "
        "duration_seconds=%.3f bytes=%d",
        asset.filename,
        duration,
        bytes_written,
    )
    emit_metric(
        f"cargo-nextest.download=ok duration_seconds={duration:.3f} "
        f"bytes={bytes_written}"
    )


def _copy_member(source: typ.IO[bytes], destination: Path) -> None:
    """Copy one archive member stream to ``destination`` and close the stream."""
    with source, destination.open("wb") as output:
        shutil.copyfileobj(source, output)


def _missing_member(executable: str, asset: ReleaseAsset) -> ValueError:
    """Build the error raised when an archive lacks the expected executable."""
    return ValueError(f"{executable} missing from {asset.filename}")


def _extract_zip_binary(archive: Path, asset: ReleaseAsset, destination: Path) -> None:
    """Extract ``cargo-nextest.exe`` from a Windows release archive."""
    executable = "cargo-nextest.exe"
    with zipfile.ZipFile(archive) as package:
        member = next(
            (name for name in package.namelist() if Path(name).name == executable),
            None,
        )
        if member is None:
            raise _missing_member(executable, asset)
        _copy_member(package.open(member), destination)


def _extract_tar_binary(archive: Path, asset: ReleaseAsset, destination: Path) -> None:
    """Extract ``cargo-nextest`` from a Linux or macOS release archive."""
    executable = "cargo-nextest"
    with tarfile.open(archive, "r:gz") as package:
        member = next(
            (
                item
                for item in package.getmembers()
                if Path(item.name).name == executable
            ),
            None,
        )
        if member is None:
            raise _missing_member(executable, asset)
        source = package.extractfile(member)
        if source is None:
            message = f"{executable} is not a file in {asset.filename}"
            raise ValueError(message)
        _copy_member(source, destination)


def _extract_binary(archive: Path, asset: ReleaseAsset, destination: Path) -> None:
    """Extract only the cargo-nextest executable from a verified archive."""
    if asset.extension == "zip":
        _extract_zip_binary(archive, asset, destination)
    else:
        _extract_tar_binary(archive, asset, destination)


def _verify_archive(archive: Path, asset: ReleaseAsset) -> None:
    """Fail unless the downloaded archive matches its pinned SHA-256."""
    actual_sha = _sha256_path(archive)
    if actual_sha != asset.sha256:
        logger.error(
            "event=nextest.archive.verify archive=%s outcome=mismatch "
            "expected=%s actual=%s",
            asset.filename,
            asset.sha256,
            actual_sha,
        )
        emit_metric("cargo-nextest.archive-digest=mismatch")
        typer.echo("cargo-nextest release archive checksum mismatch", err=True)
        raise typer.Exit(1)
    logger.info(
        "event=nextest.archive.verify archive=%s outcome=ok",
        asset.filename,
    )
    emit_metric("cargo-nextest.archive-digest=ok")


def verify_nextest_binary(path: Path, expected_sha: str) -> bool:
    """Verify the cargo-nextest binary against the expected SHA-256."""
    actual_sha = _sha256_path(path)
    if actual_sha == expected_sha:
        logger.info("event=nextest.binary.verify path=%s outcome=ok", path)
        emit_metric("cargo-nextest.binary-digest=ok")
        return True
    logger.error(
        "event=nextest.binary.verify path=%s outcome=mismatch expected=%s actual=%s",
        path,
        expected_sha,
        actual_sha,
    )
    emit_metric("cargo-nextest.binary-digest=mismatch")
    typer.echo(
        f"cargo-nextest checksum mismatch: expected {expected_sha}, got {actual_sha}",
        err=True,
    )
    return False


def _reject_mismatched_binary(
    candidate: Path,
    destination: Path,
    expected_sha: str,
) -> None:
    """Fail unless the extracted executable matches its pinned digest."""
    if verify_nextest_binary(candidate, expected_sha):
        return
    logger.error(
        "event=nextest.install destination=%s outcome=binary-mismatch",
        destination,
    )
    raise typer.Exit(1)


def install_cargo_nextest(asset: ReleaseAsset, expected_sha: str) -> Path:
    """Install a verified cargo-nextest binary from its official release."""
    suffix = ".exe" if asset.extension == "zip" else ""
    destination = _cargo_bin() / f"cargo-nextest{suffix}"
    temporary_binary = destination.with_suffix(f"{destination.suffix}.tmp")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="cargo-nextest-") as temp_dir:
            archive = Path(temp_dir) / asset.filename
            _download_archive(asset, archive)
            _verify_archive(archive, asset)
            _extract_binary(archive, asset, temporary_binary)
        _reject_mismatched_binary(temporary_binary, destination, expected_sha)
        temporary_binary.chmod(0o755)
        temporary_binary.replace(destination)
    except (OSError, tarfile.TarError, zipfile.BadZipFile, ValueError) as exc:
        logger.error(  # noqa: TRY400 - the caller converts this to an exit code.
            "event=nextest.install destination=%s outcome=failed error=%s",
            destination,
            exc,
        )
        emit_metric("cargo-nextest.install=failed")
        typer.echo(f"cargo-nextest release installation failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    except typer.Exit:
        # Raised directly by ``_download_archive``, ``_verify_archive``, or the
        # binary-mismatch check above; each already logged and echoed its own
        # specific outcome, so only the aggregate install metric is added here.
        emit_metric("cargo-nextest.install=failed")
        raise
    finally:
        temporary_binary.unlink(missing_ok=True)
    logger.info("event=nextest.install destination=%s outcome=ok", destination)
    emit_metric("cargo-nextest.install=ok")
    typer.echo("cargo-nextest official release installed and verified")
    return destination


def _prepend_to_path(directory: Path) -> None:
    """Put ``directory`` ahead of the ambient PATH, for this run and the job."""
    os.environ["PATH"] = os.pathsep.join(
        [str(directory), os.environ.get("PATH", "")],
    )
    github_path = os.environ.get("GITHUB_PATH")
    if github_path:
        with Path(github_path).open("a", encoding="utf-8") as handle:
            handle.write(f"{directory}\n")
    logger.info("event=nextest.path.prepend directory=%s", directory)


def _ensure_verified_binary_resolves(destination: Path, expected_sha: str) -> None:
    """Fail unless the binary later steps will resolve is the verified one."""
    resolved = _resolve_nextest_binary()
    if resolved is not None and _sha256_path(resolved) == expected_sha:
        logger.info(
            "event=nextest.path.resolve outcome=ok path=%s",
            resolved,
        )
        return
    logger.error(
        "event=nextest.path.resolve outcome=shadowed resolved=%s destination=%s",
        resolved,
        destination,
    )
    typer.echo(
        "cargo-nextest on PATH is "
        f"{resolved if resolved is not None else 'missing'}, "
        f"not the verified binary installed at {destination}",
        err=True,
    )
    raise typer.Exit(1)


def main() -> None:
    """Install cargo-nextest and verify the binary checksum."""
    expected_sha, asset = _release_for_platform()
    existing = _resolve_nextest_binary()
    if existing is not None and verify_nextest_binary(existing, expected_sha):
        logger.info("Using preinstalled cargo-nextest at %s", existing)
        typer.echo("cargo-nextest already installed and verified")
        # ``existing`` may have resolved via CARGO_HOME/bin even when that
        # directory is not on PATH, so later steps could not otherwise find
        # it without this.
        _prepend_to_path(existing.parent)
        emit_metric("cargo-nextest.install=reused")
        return

    # An unverified binary earlier on PATH would otherwise shadow the one just
    # installed, so later steps would run the very binary that failed
    # verification.
    destination = install_cargo_nextest(asset, expected_sha)
    _prepend_to_path(destination.parent)
    _ensure_verified_binary_resolves(destination, expected_sha)
    logger.info("cargo-nextest installation and verification succeeded")


if __name__ == "__main__":
    typer.run(main)
