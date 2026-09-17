#!/usr/bin/env python3
"""Install a manifest-resolved CodeScene coverage CLI archive safely."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import ssl
import stat
import subprocess
import sys
import tempfile
import typing as typ
import urllib.request
import zipfile
from pathlib import Path

from trusted_cli import (
    InstallError,
    Release,
    ResolutionRequest,
    RunnerPlatform,
    resolve,
    safe_member,
    validate_archive_url,
)

_VERSION = re.compile(
    r"cs-coverage version (?P<version>[^\s]+) \((?P<build>[0-9a-f]{40})\)"
)


class _RejectRedirect(urllib.request.HTTPRedirectHandler):
    """Reject redirects before urllib's default handler can follow them."""

    handler_order = 100

    def redirect_request(
        self, *args: object, **kwargs: object
    ) -> urllib.request.Request | None:
        """Refuse every 30x response from an immutable direct archive URL."""
        del args, kwargs
        error = "archive download redirects are not allowed"
        raise InstallError(error)


def extract_cli(archive: Path, release: Release, destination: Path) -> None:
    """Validate the archive and extract only its expected executable member."""
    binary = _read_trusted_binary(archive, release)
    _install_binary(binary, destination)


def _read_trusted_binary(archive: Path, release: Release) -> bytes:
    """Read the expected executable after validating every archive member."""
    try:
        with zipfile.ZipFile(archive) as bundle:
            entries = bundle.infolist()
            _validate_archive_members(entries, release)
            return bundle.read(release.member)
    except (OSError, zipfile.BadZipFile, KeyError) as error:
        message = f"cannot extract trusted CLI archive: {error}"
        raise InstallError(message) from error


def _validate_archive_members(entries: list[zipfile.ZipInfo], release: Release) -> None:
    """Reject unsafe paths before requiring the archive member set to match."""
    _reject_unsafe_entries(entries)
    names = [entry.filename for entry in entries]
    if len(names) != len(set(names)):
        _raise_unexpected_members()
    if set(names) != set(release.archive_members):
        _raise_unexpected_members()
    if len(names) != len(release.archive_members):
        _raise_unexpected_members()


def _raise_unexpected_members() -> None:
    """Raise the archive-member-set error used by the action contract."""
    message = "archive has unexpected or missing members"
    raise InstallError(message)


def _reject_unsafe_entries(entries: list[zipfile.ZipInfo]) -> None:
    """Reject unsafe archive paths and symbolic links before extraction."""
    for entry in entries:
        if not safe_member(entry.filename):
            _raise_unsafe_member()
        if stat.S_ISLNK(entry.external_attr >> 16):
            _raise_unsafe_member()


def _raise_unsafe_member() -> None:
    """Raise the archive-path error used by the action contract."""
    message = "archive member has an unsafe path or link"
    raise InstallError(message)


def _install_binary(binary: bytes, destination: Path) -> None:
    """Atomically install the validated executable at the action-owned path."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".new")
    temporary.write_bytes(binary)
    temporary.chmod(0o755)
    temporary.replace(destination)


def _download_opener() -> urllib.request.OpenerDirector:
    """Build the strict HTTPS opener used for immutable CLI archives."""
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=context), _RejectRedirect()
    )


def download_verified(release: Release, target: Path) -> None:
    """Download an approved archive over verified HTTPS and check its digest."""
    digest = _download_archive(_archive_request(release), target)
    _validate_download_digest(digest, release)


def _archive_request(release: Release) -> urllib.request.Request:
    """Create a request only after the manifest URL has been validated."""
    return urllib.request.Request(  # noqa: S310 - manifest URL is validated.
        release.archive_url, headers={"User-Agent": "shared-actions"}
    )


def _download_archive(request: urllib.request.Request, target: Path) -> str:
    """Stream an archive to disk and return its SHA-256 digest."""
    try:
        with (
            _download_opener().open(request, timeout=60) as response,
            target.open("wb") as output,
        ):
            _validate_download_url(response.geturl())
            return _copy_and_hash(response, output)
    except OSError as error:
        message = f"cannot download CLI archive: {error}"
        raise InstallError(message) from error


def _validate_download_url(url: str) -> None:
    """Defend against a custom opener returning an unapproved final URL."""
    validate_archive_url(url)


class _ReadableResponse(typ.Protocol):
    """The response interface needed to hash a streamed archive."""

    def read(self, size: int = -1) -> bytes:
        """Read up to `size` response bytes."""


def _copy_and_hash(response: _ReadableResponse, output: typ.BinaryIO) -> str:
    """Copy a response body while calculating its archive digest."""
    digest = hashlib.sha256()
    while chunk := response.read(1024 * 1024):
        digest.update(chunk)
        output.write(chunk)
    return digest.hexdigest()


def _validate_download_digest(digest: str, release: Release) -> None:
    """Reject downloaded bytes that differ from the manifest digest."""
    if digest != release.archive_sha256:
        message = "downloaded CLI archive digest does not match the manifest"
        raise InstallError(message)


def verify_version(binary: Path, release: Release) -> None:
    """Require the installed binary to report the selected logical/build pair."""
    completed = _run_version_command(binary)
    if completed.returncode:
        message = f"cs-coverage version failed with exit status {completed.returncode}"
        raise InstallError(message)
    observed = _VERSION.search(completed.stdout + completed.stderr)
    if observed is None or observed.groupdict() != {
        "version": release.version,
        "build": release.build,
    }:
        message = "installed cs-coverage version does not match the manifest"
        raise InstallError(message)


def _run_version_command(binary: Path) -> subprocess.CompletedProcess[str]:
    """Run the action-owned CLI with its network version check disabled."""
    try:
        return subprocess.run(  # noqa: S603, TID251 - execute the action-owned verified CLI.
            [str(binary), "version"],
            capture_output=True,
            check=False,
            env=os.environ | {"CS_DISABLE_VERSION_CHECK": "1"},
            text=True,
        )
    except OSError as error:
        message = "cannot start cs-coverage version command"
        raise InstallError(message) from error


def write_outputs(release: Release, output_path: Path) -> None:
    """Write GitHub Action outputs for the cache and audit log."""
    with output_path.open("a", encoding="utf-8") as output:
        output.write(f"version={release.version}\n")
        output.write(f"build={release.build}\n")
        output.write(f"platform={release.os_name}-{release.arch}\n")
        output.write(f"digest={release.archive_sha256}\n")


def log_resolution(release: Release) -> None:
    """Print the non-secret release decision for the GitHub Action log."""
    print(
        "Resolved CodeScene CLI "
        f"{release.version} ({release.build}) for {release.os_name}-{release.arch}; "
        f"archive SHA-256 {release.archive_sha256}"
    )


def main() -> None:
    """Run the requested resolution, installation, or version verification step."""
    parser = argparse.ArgumentParser()
    subcommands = parser.add_subparsers(dest="command", required=True)
    for name in ("resolve", "install", "verify"):
        command = subcommands.add_parser(name)
        command.add_argument("--manifest", type=Path, required=True)
        command.add_argument("--version", required=True)
        command.add_argument("--runner-os", required=True)
        command.add_argument("--runner-arch", required=True)
        command.add_argument("--archive-checksum", default="")
        command.add_argument("--binary", type=Path, required=name != "resolve")
        command.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    try:
        release = resolve(
            args.manifest,
            ResolutionRequest(
                args.version,
                RunnerPlatform(args.runner_os, args.runner_arch),
                args.archive_checksum,
            ),
        )
        if args.command == "resolve":
            log_resolution(release)
        if args.github_output:
            write_outputs(release, args.github_output)
        if args.command == "install":
            with tempfile.TemporaryDirectory(prefix="cs-coverage-") as directory:
                archive = Path(directory) / "archive.zip"
                download_verified(release, archive)
                extract_cli(archive, release, args.binary)
            verify_version(args.binary, release)
        elif args.command == "verify":
            verify_version(args.binary, release)
    except InstallError as error:
        print(f"cs-coverage installation refused: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
