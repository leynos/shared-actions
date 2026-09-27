#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Install a pinned, digest-verified mold release into the runner's tool cache.

``setup-rust`` runs this on a Linux runner when the caller sets
``install-mold: 'true'``. The release is chosen by version and runner
architecture from :data:`MOLD_DIGESTS`, the action-held table of SHA-256
digests, and every failure fails closed: an unlisted version or architecture,
a digest mismatch, an archive of the wrong shape, or an installed binary that
reports a different version. Nothing is placed on ``PATH`` until the archive
has been verified, unpacked and probed.

A verified tree lives under ``<tool cache>/mold/<version>-<digest>/<arch>``
with an ``<arch>.complete`` marker beside it, so a second call in the same job,
or any call on a runner whose tool cache persists, reuses it. The digest is
part of the path so that a changed pin can never be satisfied by a tree
unpacked from the old archive.

The script sets no linker flag. Choosing mold is the consumer's
``.cargo/config.toml``; this only makes ``mold`` and ``ld.mold`` resolvable.

Exit status is 0 when mold is installed or reused and 1 on any refusal, which
is reported as a GitHub ``::error`` annotation and a ``failed`` metric.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
import typing as typ
import urllib.request
from pathlib import Path

if typ.TYPE_CHECKING:
    import collections.abc as cabc

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

#: Seconds to wait for the release server to respond or to send more data.
DOWNLOAD_TIMEOUT: typ.Final = 60

#: The files a usable tree must provide, relative to its root. ``ld.mold`` is
#: the name a compiler driver looks for when asked for ``-fuse-ld=mold``.
REQUIRED_MEMBERS: typ.Final = ("bin/mold", "bin/ld.mold")


class MoldInstallError(Exception):
    """A refusal to install mold, with the reason a caller can act on."""


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
class Outcome:
    """What the installer did, and where ``mold`` now lives."""

    status: typ.Literal["installed", "cached"]
    bin_dir: Path


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
        msg = (
            f"mold is not pinned for runner architecture {runner_arch!r}; "
            f"supported: {supported}"
        )
        raise MoldInstallError(msg)
    digest = digests.get((version, arch))
    if digest is None:
        msg = (
            f"no SHA-256 is recorded for mold {version!r} on {arch}; "
            "refusing to install"
        )
        raise MoldInstallError(msg)
    return Release(version=version, arch=arch, digest=digest)


def install_dir(tool_cache: Path, release: Release) -> Path:
    """Return the tool-cache directory for *release*, keyed by version and digest."""
    return tool_cache / "mold" / f"{release.version}-{release.digest}" / release.arch


def _marker(tree: Path) -> Path:
    """Return the completion marker that sits beside *tree*."""
    return tree.parent / f"{tree.name}.complete"


def reported_version(mold: Path) -> str | None:
    """Return the version *mold* reports, or ``None`` when it cannot run.

    ``mold --version`` prints, for example, ``mold 2.41.0 (compatible with
    GNU ld)``.
    """
    try:
        # Stdlib only: the script runs on the caller's runner before any
        # dependency is installed.
        result = subprocess.run(  # noqa: S603, TID251 - runs the binary this script installed.
            [str(mold), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=DOWNLOAD_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    fields = result.stdout.split()
    if result.returncode != 0 or len(fields) < 2 or fields[0] != "mold":
        return None
    return fields[1]


def is_installed(tree: Path, release: Release) -> bool:
    """Return whether *tree* holds a complete, working install of *release*."""
    return _marker(tree).is_file() and (
        reported_version(tree / "bin" / "mold") == release.version
    )


def download(url: str, destination: Path) -> None:
    """Download *url* to *destination*.

    Raises
    ------
    MoldInstallError
        If the transfer fails for any reason.
    """
    try:
        with (
            urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT) as response,  # noqa: S310 - the scheme is the release base URL's.
            destination.open("wb") as sink,
        ):
            shutil.copyfileobj(response, sink)
    except OSError as error:
        msg = f"failed to download {url}: {error}"
        raise MoldInstallError(msg) from error


def verify(archive: Path, release: Release) -> None:
    """Check *archive* against the digest recorded for *release*.

    Raises
    ------
    MoldInstallError
        If the digest differs.
    """
    digest = hashlib.sha256()
    with archive.open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)
    actual = digest.hexdigest()
    if actual != release.digest:
        msg = (
            f"checksum mismatch for {release.archive_name}: expected "
            f"{release.digest}, got {actual}; refusing to install"
        )
        raise MoldInstallError(msg)


def unpack(archive: Path, release: Release, staging: Path) -> Path:
    """Unpack *archive* into *staging* and return its release tree.

    The ``data`` filter refuses absolute paths, parent traversal and links
    that leave the destination, so a verified archive still cannot write
    outside *staging*.

    Raises
    ------
    MoldInstallError
        If the archive cannot be read or lacks the files a caller needs.
    """
    try:
        with tarfile.open(archive, "r:gz") as bundle:
            bundle.extractall(staging, filter="data")
    except (tarfile.TarError, OSError) as error:
        msg = f"failed to unpack {release.archive_name}: {error}"
        raise MoldInstallError(msg) from error
    tree = staging / release.root_name
    missing = [member for member in REQUIRED_MEMBERS if not (tree / member).exists()]
    if missing:
        msg = f"{release.archive_name} lacks {', '.join(missing)}"
        raise MoldInstallError(msg)
    return tree


def _place(tree: Path, destination: Path) -> None:
    """Move an unpacked *tree* to *destination*, replacing any partial tree.

    *tree* is unpacked beside *destination*, so this is a rename on one
    filesystem and a reader never sees a half-copied tree.
    """
    if destination.exists():
        shutil.rmtree(destination)
    tree.rename(destination)


def install(
    release: Release,
    *,
    tool_cache: Path,
    temp_dir: Path,
    base_url: str = RELEASE_BASE_URL,
) -> Outcome:
    """Install *release* into *tool_cache*, reusing a complete earlier install.

    Raises
    ------
    MoldInstallError
        If the download, verification, unpacking or version probe fails. A
        failed install leaves no completion marker behind.
    """
    destination = install_dir(tool_cache, release)
    if is_installed(destination, release):
        return Outcome(status="cached", bin_dir=destination / "bin")
    _marker(destination).unlink(missing_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with (
        tempfile.TemporaryDirectory(dir=temp_dir, prefix="mold-") as scratch,
        tempfile.TemporaryDirectory(
            dir=destination.parent, prefix=f".{release.arch}-"
        ) as staging,
    ):
        archive = Path(scratch) / release.archive_name
        download(release.url(base_url), archive)
        verify(archive, release)
        _place(unpack(archive, release, Path(staging)), destination)
    version = reported_version(destination / "bin" / "mold")
    if version != release.version:
        msg = f"installed mold reports {version!r}, not {release.version!r}"
        raise MoldInstallError(msg)
    _marker(destination).touch()
    return Outcome(status="installed", bin_dir=destination / "bin")


def _append(path: Path | None, line: str) -> None:
    """Append *line* to a runner command file, when one is configured."""
    if path is None:
        return
    with path.open("a", encoding="utf-8") as sink:
        sink.write(f"{line}\n")


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


def main(argv: cabc.Sequence[str] | None = None) -> int:
    """Install mold as the action's step asks, and report the outcome."""
    args = _parser().parse_args(argv)
    try:
        release = select_release(args.mold_version, args.runner_arch, MOLD_DIGESTS)
        outcome = install(
            release,
            tool_cache=args.tool_cache,
            temp_dir=args.temp_dir,
            base_url=args.release_base_url,
        )
    except MoldInstallError as error:
        print("metric setup-rust.mold=failed")
        print(f"::error title=setup-rust mold::{error}", file=sys.stderr)
        return 1
    _append(args.github_path, str(outcome.bin_dir))
    _append(args.github_output, f"status={outcome.status}")
    _append(args.github_output, f"version={release.version}")
    print(f"metric setup-rust.mold={outcome.status}")
    print(
        f"::notice title=setup-rust mold::mold {release.version} "
        f"{outcome.status} for {release.arch}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
