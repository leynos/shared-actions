#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["plumbum", "typer"]
# ///
"""Install cargo-llvm-cov from the repository's tool manifest.

The manifest (``.github/tool-manifest.toml``) is the one place a tool's
version, release URL and digest are pinned, so this script resolves the entry
for the runner with the same resolver the ``install-tool`` action uses, then
downloads the archive, verifies its SHA-256, extracts only the named member and
installs it into ``CARGO_HOME/bin``. It never invokes Cargo, so a missing
prebuilt archive is a hard error rather than a source-build fallback.

An installed binary at the pinned version is reused rather than replaced, which
is what makes the archive cache and a second call cheap.
"""

from __future__ import annotations

import hashlib
import importlib.util
import logging
import os
import platform
import shutil
import tarfile
import tempfile
import time
import tomllib
import typing as typ
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import typer
from plumbum import local
from plumbum.commands.processes import CommandNotFound, ProcessExecutionError

if typ.TYPE_CHECKING:
    from types import ModuleType

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s %(message)s")

#: The version this action installs. It must name an entry in the manifest;
#: the resolver refuses a version that is not listed rather than floating.
CARGO_LLVM_COV_VERSION = "0.9.0"

TOOL_NAME = "cargo-llvm-cov"

#: The resolver reports every other failure kind; this one is ours.
MANIFEST_UNREADABLE = "manifest-unreadable"

#: Where the manifest and the shared resolver live relative to this script:
#: ``<repo>/.github/actions/<action>/scripts/<this file>``.
_GITHUB_DIR = Path(__file__).resolve().parents[3]
MANIFEST_PATH = _GITHUB_DIR / "tool-manifest.toml"
RESOLVER_PATH = _GITHUB_DIR / "actions" / "install-tool" / "scripts" / "resolve_tool.py"

# A cargo-llvm-cov release archive is under 2 MB; 200 MB bounds the disk a
# redirected endpoint could consume before the digest check rejects it.
_MAX_ARCHIVE_BYTES = 200 * 1024 * 1024

#: ``platform`` names to the ``runner.os`` / ``runner.arch`` vocabulary the
#: resolver reads, for when the script runs outside a GitHub Actions job.
_SYSTEMS = {"Linux": "Linux", "Darwin": "macOS", "Windows": "Windows"}
_MACHINES = {"x86_64": "X64", "amd64": "X64", "arm64": "ARM64", "aarch64": "ARM64"}


class ResolvedTool(typ.NamedTuple):
    """The manifest entry selected for this runner."""

    triple: str
    url: str
    sha256: str
    member: str
    extension: str
    binary: str
    version_args: tuple[str, ...]
    expected_version: str

    @property
    def filename(self) -> str:
        """Return the archive's file name."""
        return self.url.rsplit("/", 1)[-1]


def emit_metric(line: str) -> None:
    """Print one bounded metric line and append it to the job summary, if set."""
    typer.echo(f"metric {line}")
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with Path(summary_path).open("a", encoding="utf-8") as handle:
        handle.write(f"metric {line}\n")


def _load_resolver() -> ModuleType:
    """Import the install-tool resolver from its own script directory."""
    spec = importlib.util.spec_from_file_location("resolve_tool", RESOLVER_PATH)
    if spec is None or spec.loader is None:
        typer.echo(f"cannot load the tool resolver at {RESOLVER_PATH}", err=True)
        raise typer.Exit(1)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def runner_description() -> tuple[str, str]:
    """Return the runner OS and architecture as GitHub Actions names them.

    ``RUNNER_OS`` and ``RUNNER_ARCH`` are authoritative inside a job; outside
    one they are derived from ``platform`` so the script can be run locally.
    """
    runner_os = os.environ.get("RUNNER_OS") or _SYSTEMS.get(platform.system(), "")
    runner_arch = os.environ.get("RUNNER_ARCH") or _MACHINES.get(
        platform.machine().lower(), ""
    )
    return runner_os, runner_arch


class ToolResolutionError(Exception):
    """The manifest offers no usable entry for this tool, version and runner.

    ``kind`` is one of the resolver's closed set of reasons, so the caller can
    publish it as a bounded metric without inspecting the message.
    """

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def load_manifest(manifest_path: Path = MANIFEST_PATH) -> dict[str, object]:
    """Read the tool manifest, or raise ``ToolResolutionError``."""
    try:
        with manifest_path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        message = f"could not read the tool manifest {manifest_path}: {exc}"
        raise ToolResolutionError(MANIFEST_UNREADABLE, message) from exc


def resolve_tool(
    version: str = CARGO_LLVM_COV_VERSION,
    *,
    manifest: dict[str, object] | None = None,
    runner: tuple[str, str] | None = None,
) -> ResolvedTool:
    """Return the manifest entry for ``version`` on ``runner``.

    A query with no side effects: it reads the manifest (or the one passed
    in) and either returns the entry or raises ``ToolResolutionError``.
    """
    resolver = _load_resolver()
    if manifest is None:
        manifest = load_manifest()
    schema = manifest.get("schema")
    if schema != resolver.SCHEMA:
        # The generic install-tool action fails closed on a schema it does
        # not read; calling the resolver directly must not skip that check,
        # or a later layout with plausible old fields would resolve wrongly.
        message = (
            f"the tool manifest declares schema {schema!r}; this installer "
            f"reads schema {resolver.SCHEMA}"
        )
        raise ToolResolutionError(resolver.UNSUPPORTED_SCHEMA, message)
    runner_os, runner_arch = runner or runner_description()
    fields = resolver.resolve(
        manifest, TOOL_NAME, version, resolver.Runner(runner_os, runner_arch)
    )
    if fields.get("status") != "ok":
        kind = str(fields.get("error_kind"))
        message = str(fields.get("error_message"))
        raise ToolResolutionError(kind, message)
    return ResolvedTool(
        triple=str(fields["triple"]),
        url=str(fields["url"]),
        sha256=str(fields["sha256"]),
        member=str(fields["member"]),
        extension=str(fields["extension"]),
        binary=str(fields["binary"]),
        version_args=tuple(str(fields["version_args"]).split()),
        expected_version=str(fields["expected_version"]),
    )


def _sha256_path(path: Path) -> str:
    """Compute the SHA-256 digest for ``path``."""
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def cargo_bin() -> Path:
    """Return the Cargo binary directory honoured by the caller."""
    cargo_home = Path(os.environ.get("CARGO_HOME", Path.home() / ".cargo"))
    return cargo_home / "bin"


def reported_version(binary: Path, version_args: tuple[str, ...]) -> str | None:
    """Return the version line ``binary`` reports, or None if it cannot run."""
    if not version_args:
        return None
    try:
        output = local[str(binary)][list(version_args)](timeout=60)
    except (OSError, CommandNotFound, ProcessExecutionError):
        return None
    text = str(output).strip()
    return text.splitlines()[0] if text else ""


def installed_at_pinned_version(destination: Path, tool: ResolvedTool) -> bool:
    """Whether ``destination`` already holds the binary at the pinned version."""
    if not destination.is_file():
        return False
    return reported_version(destination, tool.version_args) == tool.expected_version


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
    """Copy ``source`` to ``destination`` in chunks, up to ``max_bytes``."""
    total = 0
    while True:
        chunk = source.read(chunk_size)
        if not chunk:
            return total
        total += len(chunk)
        if total > max_bytes:
            raise _ArchiveTooLargeError(total)
        destination.write(chunk)


def download_archive(tool: ResolvedTool, destination: Path) -> None:
    """Download the pinned release archive to ``destination``."""
    # The URL comes from the manifest, whose contract test holds every URL to
    # https on a release host, so non-HTTPS schemes cannot reach this boundary.
    request = urllib.request.Request(  # noqa: S310
        tool.url, headers={"User-Agent": "generate-coverage"}
    )
    logger.info(
        "event=llvm-cov.download.start archive=%s url=%s", tool.filename, tool.url
    )
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
        emit_metric(
            f"cargo-llvm-cov.download=failed duration_seconds={duration:.3f} bytes=0"
        )
        typer.echo(
            f"cargo-llvm-cov release archive exceeded {_MAX_ARCHIVE_BYTES} bytes "
            "and was discarded",
            err=True,
        )
        raise typer.Exit(1) from exc
    except (OSError, urllib.error.URLError) as exc:
        duration = time.monotonic() - started
        emit_metric(
            f"cargo-llvm-cov.download=failed duration_seconds={duration:.3f} bytes=0"
        )
        typer.echo(f"cargo-llvm-cov release download failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    duration = time.monotonic() - started
    logger.info(
        "event=llvm-cov.download.finish archive=%s outcome=ok "
        "duration_seconds=%.3f bytes=%d",
        tool.filename,
        duration,
        bytes_written,
    )
    emit_metric(
        f"cargo-llvm-cov.download=ok duration_seconds={duration:.3f} "
        f"bytes={bytes_written}"
    )


def verify_archive(archive: Path, tool: ResolvedTool) -> None:
    """Fail unless the downloaded archive matches the manifest digest."""
    actual = _sha256_path(archive)
    if actual != tool.sha256:
        logger.error(
            "event=llvm-cov.archive.verify archive=%s outcome=mismatch "
            "expected=%s actual=%s",
            tool.filename,
            tool.sha256,
            actual,
        )
        emit_metric("cargo-llvm-cov.archive-digest=mismatch")
        typer.echo("cargo-llvm-cov release archive checksum mismatch", err=True)
        raise typer.Exit(1)
    emit_metric("cargo-llvm-cov.archive-digest=ok")


def _copy_member(source: typ.IO[bytes], destination: Path) -> None:
    """Copy one archive member stream to ``destination`` and close the stream."""
    with source, destination.open("wb") as output:
        shutil.copyfileobj(source, output)


def extract_member(archive: Path, tool: ResolvedTool, destination: Path) -> None:
    """Extract exactly the manifest's ``member`` from a verified archive."""
    if tool.extension == "zip":
        with zipfile.ZipFile(archive) as package:
            if tool.member not in package.namelist():
                message = f"{tool.member} missing from {tool.filename}"
                raise ValueError(message)
            _copy_member(package.open(tool.member), destination)
        return
    with tarfile.open(archive, "r:gz") as package:
        try:
            member = package.getmember(tool.member)
        except KeyError as exc:
            message = f"{tool.member} missing from {tool.filename}"
            raise ValueError(message) from exc
        source = package.extractfile(member)
        if source is None:
            message = f"{tool.member} is not a file in {tool.filename}"
            raise ValueError(message)
        _copy_member(source, destination)


def install(
    tool: ResolvedTool,
    destination: Path,
    *,
    fetch: typ.Callable[[ResolvedTool, Path], None] = download_archive,
) -> None:
    """Download, verify and install the binary.

    ``destination`` is left intact when any step before the final move fails.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Staged in the destination's own directory so the final publish is a
    # rename on one filesystem: a temporary directory elsewhere would turn
    # the move into copy-then-delete, during which a concurrent reader could
    # open a half-written executable.
    with tempfile.TemporaryDirectory(
        prefix=".cargo-llvm-cov-staging-", dir=destination.parent
    ) as workdir:
        archive = Path(workdir) / tool.filename
        fetch(tool, archive)
        verify_archive(archive, tool)
        staged = Path(workdir) / tool.binary
        try:
            extract_member(archive, tool, staged)
        except (ValueError, tarfile.TarError, zipfile.BadZipFile) as exc:
            emit_metric("cargo-llvm-cov.install=failed")
            typer.echo(f"cargo-llvm-cov archive extraction failed: {exc}", err=True)
            raise typer.Exit(1) from exc
        staged.chmod(0o755)
        staged.replace(destination)
    reported = reported_version(destination, tool.version_args)
    if reported != tool.expected_version:
        emit_metric("cargo-llvm-cov.install=version-mismatch")
        typer.echo(
            f"installed cargo-llvm-cov reports {reported!r}, expected "
            f"{tool.expected_version!r}",
            err=True,
        )
        raise typer.Exit(1)
    emit_metric("cargo-llvm-cov.install=ok")


def export_path(directory: Path) -> None:
    """Add ``directory`` to the job's PATH for later steps, if in a job."""
    github_path = os.environ.get("GITHUB_PATH")
    if not github_path:
        return
    with Path(github_path).open("a", encoding="utf-8") as handle:
        handle.write(f"{directory}\n")


def main() -> None:
    """Install cargo-llvm-cov at the pinned version from the tool manifest."""
    try:
        tool = resolve_tool()
    except ToolResolutionError as exc:
        emit_metric(f"cargo-llvm-cov.resolve={exc.kind}")
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    emit_metric("cargo-llvm-cov.resolve=ok")
    destination = cargo_bin() / tool.binary
    if installed_at_pinned_version(destination, tool):
        emit_metric("cargo-llvm-cov.install=reused")
        typer.echo(f"cargo-llvm-cov {CARGO_LLVM_COV_VERSION} already installed")
    else:
        install(tool, destination)
        typer.echo(
            f"cargo-llvm-cov {CARGO_LLVM_COV_VERSION} installed to {destination}"
        )
    export_path(destination.parent)


if __name__ == "__main__":
    typer.run(main)
