#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
# Python 3.12 rather than the scripting standard's 3.13, by the standard's
# documented-exception rule: this runs on the caller's runner, where Ubuntu
# 24.04's system interpreter is 3.12, so a 3.13 floor would make uv download
# an interpreter on every job that asks for mold. Nothing here needs 3.13.
"""Install a pinned, digest-verified mold release into the runner's tool cache.

``setup-rust`` runs this on a Linux runner when the caller sets
``install-mold: 'true'``. The release is chosen by version and runner
architecture from :data:`MOLD_DIGESTS`, the action-held table of SHA-256
digests, and every failure fails closed: an unlisted version or architecture,
a failed download, a digest mismatch, an archive of the wrong shape, an
installed binary that reports a different version, or a filesystem error.
Nothing is placed on ``PATH`` until the archive has been verified, unpacked
and probed.

A verified tree lives under ``<tool cache>/mold/<version>-<digest>/<arch>``
with an ``<arch>.complete`` marker beside it, so a second call in the same job,
or any call on a runner whose tool cache persists, reuses it. The digest is
part of the path so that a changed pin can never be satisfied by a tree
unpacked from the old archive, and a marked tree is reused only while every
required file is present and the binary still reports the pinned version.
Otherwise it is stale and is reinstalled.

The script sets no linker flag. Choosing mold is the consumer's
``.cargo/config.toml``; this only makes ``mold`` and ``ld.mold`` resolvable.

Exit status is 0 when mold is installed or reused and 1 on any refusal. Every
run prints bounded ``metric setup-rust.mold...`` lines: the outcome, the cache
state, an elapsed-time bucket and, on failure, one failure category. None of
them carries a URL, a path or an error message.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import http.client
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import typing as typ
from pathlib import Path

from mold_adapters import open_url, run_binary

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from mold_adapters import OpenUrl, RunBinary

#: SHA-256 of each pinned release archive, by version and mold architecture.
#: Each digest was computed from an independent download of the archive and
#: agrees with the digest GitHub records for the release asset and with
#: netsuke's ``tools/mold/SHA256SUMS``. A new version needs both
#: architectures recorded here before a caller can ask for it.
MOLD_DIGESTS: typ.Final[cabc.Mapping[tuple[str, str], str]] = {
    ("2.41.0", "x86_64"): (
        "a3696680d99e692970590a178bc3a33d78d60d1c6dc9db7a11b557b02b751f5d"
    ),
    ("2.41.0", "aarch64"): (
        "946de2774b06a71346bd59b55fddba610b65b8d93c3a4a1559cc84e103472710"
    ),
}

#: ``runner.arch`` to the architecture in mold's archive names. Anything else
#: is refused rather than guessed at.
RUNNER_ARCHES: typ.Final[cabc.Mapping[str, str]] = {
    "X64": "x86_64",
    "ARM64": "aarch64",
}

RELEASE_BASE_URL: typ.Final = "https://github.com/rui314/mold/releases/download"

#: Seconds to wait for the release server, or for the version probe.
TIMEOUT_SECONDS: typ.Final = 60.0

#: The files a usable tree must provide, relative to its root. ``ld.mold`` is
#: the name a compiler driver looks for when asked for ``-fuse-ld=mold``.
REQUIRED_MEMBERS: typ.Final = ("bin/mold", "bin/ld.mold")

#: Upper bounds, in seconds, of the elapsed-time buckets reported as a metric.
ELAPSED_BUCKETS: typ.Final = ((5, "lt5s"), (30, "lt30s"), (120, "lt120s"))

#: Why an install was refused. Closed, so the failure metric stays bounded.
FailureCategory = typ.Literal[
    "unsupported-arch",
    "unpinned-version",
    "download",
    "digest",
    "archive",
    "version",
    "filesystem",
    "runner-files",
]
#: What the tool cache held before the install: nothing, a usable tree, or a
#: marked tree that no longer passes the checks.
CacheState = typ.Literal["miss", "hit", "stale"]


class MoldInstallError(Exception):
    """A refusal to install mold, with an actionable reason.

    Each subclass names one bounded :data:`FailureCategory`, which the
    failure metric reports in place of the reason.
    """

    category: typ.ClassVar[FailureCategory]


class UnsupportedArchError(MoldInstallError):
    """The runner's architecture has no pinned mold release."""

    category = "unsupported-arch"


class UnpinnedVersionError(MoldInstallError):
    """The requested version has no recorded digest."""

    category = "unpinned-version"


class DownloadError(MoldInstallError):
    """The release archive could not be downloaded."""

    category = "download"


class DigestError(MoldInstallError):
    """The archive's digest differs from the recorded one."""

    category = "digest"


class ArchiveError(MoldInstallError):
    """The archive could not be unpacked or lacks a required file."""

    category = "archive"


class VersionMismatchError(MoldInstallError):
    """The installed binary does not report the pinned version."""

    category = "version"


class CacheFilesystemError(MoldInstallError):
    """The tool cache or scratch space could not be read or written."""

    category = "filesystem"


class RunnerFilesError(MoldInstallError):
    """The runner's ``GITHUB_PATH`` or ``GITHUB_OUTPUT`` could not be written."""

    category = "runner-files"


@dataclasses.dataclass(frozen=True)
class Release:
    """One pinned mold archive: its version, architecture and digest."""

    version: str
    arch: str
    digest: str

    @property
    def root_name(self) -> str:
        """Return the archive's single top-level directory name."""
        return f"mold-{self.version}-{self.arch}-linux"

    @property
    def archive_name(self) -> str:
        """Return the release asset's file name."""
        return f"{self.root_name}.tar.gz"

    def url(self, base_url: str) -> str:
        """Return the asset's download URL under *base_url*."""
        return f"{base_url.rstrip('/')}/v{self.version}/{self.archive_name}"


@dataclasses.dataclass(frozen=True)
class Probe:
    """What running ``mold --version`` found.

    Exactly one of ``version`` and ``failure`` is set, so a probe failure is
    kept rather than read as an absent install.
    """

    version: str | None = None
    failure: typ.Literal["unrunnable", "timeout", "exit-status", "not-mold"] | None = (
        None
    )


@dataclasses.dataclass(frozen=True)
class Outcome:
    """What the installer did, what it found, and where ``mold`` now lives."""

    status: typ.Literal["installed", "cached"]
    cache: CacheState
    bin_dir: Path


@dataclasses.dataclass(frozen=True)
class Adapters:
    """The process and network boundaries the installer crosses.

    ``base_url`` rides here because it names where the network boundary
    reaches, and keeps the install entry points to four parameters.
    """

    run: RunBinary = run_binary
    fetch: OpenUrl = open_url
    base_url: str = RELEASE_BASE_URL


def select_release(
    version: str,
    runner_arch: str,
    digests: cabc.Mapping[tuple[str, str], str] = MOLD_DIGESTS,
) -> Release:
    """Return the pinned release for *version* on *runner_arch*.

    Examples
    --------
    >>> select_release("2.41.0", "X64").archive_name
    'mold-2.41.0-x86_64-linux.tar.gz'

    Raises
    ------
    MoldInstallError
        If the architecture is unsupported or no digest is recorded.
    """
    arch = RUNNER_ARCHES.get(runner_arch)
    if arch is None:
        supported = ", ".join(sorted(RUNNER_ARCHES))
        reason = (
            f"mold is not pinned for runner architecture {runner_arch!r}; "
            f"supported: {supported}"
        )
        raise UnsupportedArchError(reason)
    digest = digests.get((version, arch))
    if digest is None:
        reason = (
            f"no SHA-256 is recorded for mold {version!r} on {arch}; "
            "refusing to install"
        )
        raise UnpinnedVersionError(reason)
    return Release(version=version, arch=arch, digest=digest)


def install_dir(tool_cache: Path, release: Release) -> Path:
    """Return the tool-cache directory for *release*, keyed by version and digest."""
    return tool_cache / "mold" / f"{release.version}-{release.digest}" / release.arch


def _marker(tree: Path) -> Path:
    """Return the completion marker that sits beside *tree*."""
    return tree.parent / f"{tree.name}.complete"


def _version_from_banner(banner: str) -> str | None:
    """Return the version from a ``mold --version`` banner, or ``None``.

    Examples
    --------
    >>> _version_from_banner("mold 2.41.0 (compatible with GNU ld)")
    '2.41.0'
    >>> _version_from_banner("GNU ld 2.42") is None
    True
    """
    match banner.split()[:2]:
        case ["mold", version]:
            return version
        case _:
            return None


def probe(mold: Path, run: RunBinary = run_binary) -> Probe:
    """Ask *mold* for its version, keeping any failure's kind.

    A query: it runs the binary and reads its banner, and changes nothing.
    """
    try:
        result = run([str(mold), "--version"], TIMEOUT_SECONDS)
    except OSError:
        return Probe(failure="unrunnable")
    except subprocess.TimeoutExpired:
        return Probe(failure="timeout")
    if result.returncode != 0:
        return Probe(failure="exit-status")
    version = _version_from_banner(result.stdout)
    return Probe(version=version) if version else Probe(failure="not-mold")


def cache_state(
    tree: Path, release: Release, run: RunBinary = run_binary
) -> CacheState:
    """Classify what the tool cache holds for *release* at *tree*.

    A query. ``stale`` means a completion marker whose tree has lost a
    required file or whose binary no longer reports the pinned version.
    """
    if not _marker(tree).is_file():
        return "miss"
    if not all((tree / member).exists() for member in REQUIRED_MEMBERS):
        return "stale"
    reported = probe(tree / "bin" / "mold", run)
    return "hit" if reported.version == release.version else "stale"


def download(url: str, destination: Path, fetch: OpenUrl = open_url) -> None:
    """Download *url* to *destination*.

    Raises
    ------
    MoldInstallError
        If the transfer fails for any reason, including a response cut short
        mid-chunk, which ``http.client`` reports outside ``OSError``.
    """
    try:
        with fetch(url, TIMEOUT_SECONDS) as response, destination.open("wb") as sink:
            shutil.copyfileobj(response, sink)
    except (OSError, http.client.HTTPException) as error:
        reason = f"failed to download {url}: {error}"
        raise DownloadError(reason) from error


def verify(archive: Path, release: Release) -> None:
    """Check *archive* against the digest recorded for *release*.

    Raises
    ------
    MoldInstallError
        If the archive cannot be read or its digest differs.
    """
    digest = hashlib.sha256()
    try:
        with archive.open("rb") as source:
            for block in iter(lambda: source.read(1 << 20), b""):
                digest.update(block)
    except OSError as error:
        reason = f"failed to read {release.archive_name}: {error}"
        raise CacheFilesystemError(reason) from error
    actual = digest.hexdigest()
    if actual != release.digest:
        reason = (
            f"checksum mismatch for {release.archive_name}: expected "
            f"{release.digest}, got {actual}; refusing to install"
        )
        raise DigestError(reason)


def _extract(archive: Path, release: Release, staging: Path) -> None:
    """Extract *archive* into *staging* through the ``data`` filter."""
    try:
        with tarfile.open(archive, "r:gz") as bundle:
            bundle.extractall(staging, filter="data")
    except (tarfile.TarError, OSError) as error:
        reason = f"failed to unpack {release.archive_name}: {error}"
        raise ArchiveError(reason) from error


def _require_shape(release: Release, staging: Path) -> Path:
    """Return the release tree under *staging* once its shape is confirmed."""
    strays = [e for e in staging.iterdir() if e.name != release.root_name]
    if strays:
        reason = f"{release.archive_name} has entries outside {release.root_name}/"
        raise ArchiveError(reason)
    tree = staging / release.root_name
    missing = [member for member in REQUIRED_MEMBERS if not (tree / member).exists()]
    if missing:
        reason = f"{release.archive_name} lacks {', '.join(missing)}"
        raise ArchiveError(reason)
    return tree


def unpack(archive: Path, release: Release, staging: Path) -> Path:
    """Unpack *archive* into *staging* and return its release tree.

    The ``data`` filter refuses parent traversal and links that are absolute
    or leave the destination, and strips a leading ``/`` from member names,
    so a verified archive still cannot write outside *staging*. Anything that
    lands beside the release's single top-level directory, such as a
    formerly absolute name, is then refused as the wrong shape.

    Raises
    ------
    MoldInstallError
        If the archive cannot be read or lacks the files a caller needs.
    """
    _extract(archive, release, staging)
    return _require_shape(release, staging)


def _place(tree: Path, destination: Path) -> None:
    """Move an unpacked *tree* to *destination*, replacing any partial tree.

    *tree* is unpacked beside *destination*, so this is a rename on one
    filesystem and a reader never sees a half-copied tree.
    """
    if destination.exists():
        shutil.rmtree(destination)
    tree.rename(destination)


def _fetch_and_place(
    release: Release,
    destination: Path,
    *,
    temp_dir: Path,
    adapters: Adapters,
) -> None:
    """Download, verify and unpack *release*, then rename it to *destination*."""
    temp_dir.mkdir(parents=True, exist_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with (
        tempfile.TemporaryDirectory(dir=temp_dir, prefix="mold-") as scratch,
        tempfile.TemporaryDirectory(
            dir=destination.parent, prefix=f".{release.arch}-"
        ) as staging,
    ):
        archive = Path(scratch) / release.archive_name
        download(release.url(adapters.base_url), archive, adapters.fetch)
        verify(archive, release)
        _place(unpack(archive, release, Path(staging)), destination)


def install(
    release: Release,
    *,
    tool_cache: Path,
    temp_dir: Path,
    adapters: Adapters | None = None,
) -> Outcome:
    """Install *release* into *tool_cache*, reusing a usable earlier install.

    Raises
    ------
    MoldInstallError
        If the download, verification, unpacking, version probe or any
        filesystem operation fails. A failed install leaves no completion
        marker behind.
    """
    adapters = adapters or Adapters()
    destination = install_dir(tool_cache, release)
    state = cache_state(destination, release, adapters.run)
    if state == "hit":
        return Outcome(status="cached", cache=state, bin_dir=destination / "bin")
    try:
        _marker(destination).unlink(missing_ok=True)
        _fetch_and_place(
            release,
            destination,
            temp_dir=temp_dir,
            adapters=adapters,
        )
        reported = probe(destination / "bin" / "mold", adapters.run)
        if reported.version != release.version:
            found = reported.version or reported.failure
            reason = f"installed mold reports {found!r}, not {release.version!r}"
            raise VersionMismatchError(reason)
        _marker(destination).touch()
    except OSError as error:
        reason = f"filesystem error while installing mold: {error.strerror or error}"
        raise CacheFilesystemError(reason) from error
    return Outcome(status="installed", cache=state, bin_dir=destination / "bin")


def elapsed_bucket(seconds: float) -> str:
    """Return the bounded bucket name for an elapsed time in *seconds*.

    Examples
    --------
    >>> elapsed_bucket(0.4), elapsed_bucket(45), elapsed_bucket(600)
    ('lt5s', 'lt120s', 'ge120s')
    """
    for bound, name in ELAPSED_BUCKETS:
        if seconds < bound:
            return name
    return "ge120s"


def _append(path: Path | None, line: str) -> None:
    """Append *line* to a runner command file, when one is configured."""
    if path is None:
        return
    with path.open("a", encoding="utf-8") as sink:
        sink.write(f"{line}\n")


def _report(outcome: Outcome, release: Release, args: argparse.Namespace) -> None:
    """Put mold on ``PATH`` and write the step's outputs.

    Raises
    ------
    MoldInstallError
        If a runner command file cannot be written.
    """
    try:
        _append(args.github_path, str(outcome.bin_dir))
        _append(args.github_output, f"status={outcome.status}")
        _append(args.github_output, f"version={release.version}")
    except OSError as error:
        reason = f"cannot write the runner's command files: {error.strerror or error}"
        raise RunnerFilesError(reason) from error


def _parser() -> argparse.ArgumentParser:
    """Return the command-line parser for the action's install step."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mold-version", required=True)
    parser.add_argument("--runner-arch", required=True)
    parser.add_argument("--tool-cache", required=True, type=Path)
    parser.add_argument("--temp-dir", required=True, type=Path)
    parser.add_argument("--github-path", type=Path)
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--release-base-url", default=RELEASE_BASE_URL)
    return parser


def main(
    argv: cabc.Sequence[str] | None = None,
    adapters: Adapters | None = None,
    clock: typ.Callable[[], float] = time.monotonic,
) -> int:
    """Install mold as the action's step asks, and report the outcome."""
    args = _parser().parse_args(argv)
    started = clock()
    try:
        release = select_release(args.mold_version, args.runner_arch, MOLD_DIGESTS)
        outcome = install(
            release,
            tool_cache=args.tool_cache,
            temp_dir=args.temp_dir,
            adapters=dataclasses.replace(
                adapters or Adapters(), base_url=args.release_base_url
            ),
        )
        _report(outcome, release, args)
    except MoldInstallError as error:
        print("metric setup-rust.mold=failed")
        print(f"metric setup-rust.mold.failure={error.category}")
        print(f"metric setup-rust.mold.seconds={elapsed_bucket(clock() - started)}")
        print(f"::error title=setup-rust mold::{error}", file=sys.stderr)
        return 1
    print(f"metric setup-rust.mold={outcome.status}")
    print(f"metric setup-rust.mold.cache={outcome.cache}")
    print(f"metric setup-rust.mold.seconds={elapsed_bucket(clock() - started)}")
    print(
        f"::notice title=setup-rust mold::mold {release.version} "
        f"{outcome.status} for {release.arch} (tool cache: {outcome.cache})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
