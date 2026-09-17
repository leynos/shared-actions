#!/usr/bin/env python3
"""Resolve, verify, and install a trusted CodeScene coverage CLI archive."""

from __future__ import annotations

import argparse
import dataclasses as dc
import hashlib
import json
import os
import re
import ssl
import stat
import sys
import tempfile
import typing as typ
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

if typ.TYPE_CHECKING:
    import http.client as http_client

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_BUILD = re.compile(r"[0-9a-f]{40}\Z")
_VERSION = re.compile(
    r"cs-coverage version (?P<version>[^\s]+) \((?P<build>[0-9a-f]{40})\)"
)
_DOWNLOAD_HOST = "downloads.codescene.io"


class InstallError(RuntimeError):
    """Raised when a coverage CLI installation cannot be trusted."""


class _ApprovedRedirect(urllib.request.HTTPRedirectHandler):
    """Follow only same-host HTTPS redirects from CodeScene's archive host."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: typ.IO[bytes],
        code: int,
        msg: str,
        headers: http_client.HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        """Reject a redirect before urllib can request an unapproved URL."""
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.scheme != "https" or parsed.hostname != _DOWNLOAD_HOST:
            error = "archive download redirect is not approved HTTPS"
            raise InstallError(error)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@dc.dataclass(frozen=True)
class Release:
    """One immutable CLI archive accepted by the action."""

    version: str
    build: str
    os_name: str
    arch: str
    archive_url: str
    archive_sha256: str
    member: str
    archive_members: tuple[str, ...]


def _required_string(data: dict[str, object], key: str) -> str:
    """Return a non-empty string field or fail without a fallback."""
    value = data.get(key)
    if not isinstance(value, str) or not value:
        error = f"manifest entry has no usable {key!r}"
        raise InstallError(error)
    return value


def load_manifest(path: Path) -> tuple[Release, ...]:
    """Load and validate the committed archive trust anchor."""
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        message = f"cannot read CLI manifest {path}: {error}"
        raise InstallError(message) from error
    if not isinstance(loaded, dict) or loaded.get("schema_version") != 1:
        message = "CLI manifest has an unsupported schema"
        raise InstallError(message)
    entries = loaded.get("releases")
    if not isinstance(entries, list) or not entries:
        message = "CLI manifest has no releases"
        raise InstallError(message)
    releases: list[Release] = []
    for entry in entries:
        if not isinstance(entry, dict):
            message = "CLI manifest contains a non-object release"
            raise InstallError(message)
        platform = entry.get("platform")
        members = entry.get("archive_members")
        if not isinstance(platform, dict) or not isinstance(members, list):
            message = "manifest release has invalid platform or members"
            raise InstallError(message)
        release = Release(
            version=_required_string(entry, "version"),
            build=_required_string(entry, "build"),
            os_name=_required_string(platform, "os"),
            arch=_required_string(platform, "arch"),
            archive_url=_required_string(entry, "archive_url"),
            archive_sha256=_required_string(entry, "archive_sha256"),
            member=_required_string(entry, "member"),
            archive_members=tuple(
                member for member in members if isinstance(member, str) and member
            ),
        )
        if not _BUILD.fullmatch(release.build):
            message = "manifest release has an invalid build identifier"
            raise InstallError(message)
        if not _SHA256.fullmatch(release.archive_sha256):
            message = "manifest release has a missing or invalid archive digest"
            raise InstallError(message)
        parsed = urllib.parse.urlsplit(release.archive_url)
        if parsed.scheme != "https" or parsed.hostname != _DOWNLOAD_HOST:
            message = "manifest archive URL is not an approved HTTPS download"
            raise InstallError(message)
        if (
            len(release.archive_members) != len(members)
            or len(set(release.archive_members)) != len(release.archive_members)
            or any(not _safe_member(member) for member in release.archive_members)
        ):
            message = "manifest release has an invalid archive member"
            raise InstallError(message)
        if not release.member or release.member not in release.archive_members:
            message = "manifest release has no expected archive member"
            raise InstallError(message)
        releases.append(release)
    return tuple(releases)


def resolve(
    manifest: Path,
    version: str,
    runner_os: str,
    runner_arch: str,
    caller_checksum: str = "",
) -> Release:
    """Select exactly one approved release and reject caller disagreement."""
    requested = version.strip()
    if not requested:
        message = "cli-version must name a manifest version"
        raise InstallError(message)
    matches = [
        entry
        for entry in load_manifest(manifest)
        if entry.version == requested
        and entry.os_name == runner_os.lower()
        and entry.arch == runner_arch.lower()
    ]
    if not matches:
        message = (
            f"no approved cs-coverage archive for version {requested!r} on "
            f"{runner_os}/{runner_arch}"
        )
        raise InstallError(message)
    if len(matches) != 1:
        message = "CLI manifest resolves ambiguously"
        raise InstallError(message)
    release = matches[0]
    supplied = caller_checksum.strip().lower()
    if supplied and supplied != release.archive_sha256:
        message = "caller archive checksum conflicts with the manifest"
        raise InstallError(message)
    return release


def _safe_member(name: str) -> bool:
    """Return whether a zip member is a single safe file name."""
    path = Path(name)
    return (
        "\\" not in name
        and name not in {".", ".."}
        and not path.is_absolute()
        and ".." not in path.parts
        and len(path.parts) == 1
    )


def extract_cli(archive: Path, release: Release, destination: Path) -> None:
    """Validate the archive and extract only its expected executable member."""
    try:
        with zipfile.ZipFile(archive) as bundle:
            entries = bundle.infolist()
            names = [entry.filename for entry in entries]
            allowed = set(release.archive_members)
            if (
                len(names) != len(set(names))
                or set(names) != allowed
                or len(names) != len(allowed)
            ):
                message = "archive has unexpected or missing members"
                raise InstallError(message)
            for entry in entries:
                mode = entry.external_attr >> 16
                if not _safe_member(entry.filename) or stat.S_ISLNK(mode):
                    message = "archive member has an unsafe path or link"
                    raise InstallError(message)
            binary = bundle.read(release.member)
    except (OSError, zipfile.BadZipFile, KeyError) as error:
        message = f"cannot extract trusted CLI archive: {error}"
        raise InstallError(message) from error
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
        urllib.request.HTTPSHandler(context=context), _ApprovedRedirect()
    )


def download_verified(release: Release, target: Path) -> None:
    """Download an approved archive over verified HTTPS and check its digest."""
    request = urllib.request.Request(  # noqa: S310 - manifest URL is validated.
        release.archive_url, headers={"User-Agent": "shared-actions"}
    )
    digest = hashlib.sha256()
    try:
        with (
            _download_opener().open(request, timeout=60) as response,
            target.open("wb") as output,
        ):
            resolved = urllib.parse.urlsplit(response.geturl())
            if resolved.scheme != "https" or resolved.hostname != _DOWNLOAD_HOST:
                message = "archive download redirected outside approved HTTPS"
                raise InstallError(message)
            while chunk := response.read(1024 * 1024):
                digest.update(chunk)
                output.write(chunk)
    except OSError as error:
        message = f"cannot download CLI archive: {error}"
        raise InstallError(message) from error
    if digest.hexdigest() != release.archive_sha256:
        message = "downloaded CLI archive digest does not match the manifest"
        raise InstallError(message)


def verify_version(binary: Path, release: Release) -> None:
    """Require the installed binary to report the selected logical/build pair."""
    import subprocess

    completed = subprocess.run(  # noqa: S603, TID251 - execute the action-owned verified CLI.
        [str(binary), "version"],
        capture_output=True,
        check=False,
        env=os.environ | {"CS_DISABLE_VERSION_CHECK": "1"},
        text=True,
    )
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
            args.version,
            args.runner_os,
            args.runner_arch,
            args.archive_checksum,
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
