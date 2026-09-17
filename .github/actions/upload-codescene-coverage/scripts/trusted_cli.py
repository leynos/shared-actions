"""Model and resolve manifest-pinned CodeScene coverage CLI releases."""

from __future__ import annotations

import dataclasses as dc
import json
import re
import typing as typ
import urllib.parse
from pathlib import Path

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_BUILD = re.compile(r"[0-9a-f]{40}\Z")
_DOWNLOAD_HOST = "downloads.codescene.io"


class InstallError(RuntimeError):
    """Raised when a coverage CLI installation cannot be trusted."""


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


@dc.dataclass(frozen=True)
class RunnerPlatform:
    """The operating-system and architecture pair reported by GitHub Actions."""

    os_name: str
    arch: str


@dc.dataclass(frozen=True)
class ResolutionRequest:
    """The caller's requested release and optional digest assertion."""

    version: str
    platform: RunnerPlatform
    caller_checksum: str = ""


def _required_string(data: dict[str, object], key: str) -> str:
    """Return a non-empty string field or fail without a fallback."""
    value = data.get(key)
    if not isinstance(value, str) or not value:
        error = f"manifest entry has no usable {key!r}"
        raise InstallError(error)
    return value


def _read_manifest(path: Path) -> object:
    """Read a manifest without interpreting its schema."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        message = f"cannot read CLI manifest {path}: {error}"
        raise InstallError(message) from error


def _release_entries(loaded: object) -> list[dict[str, object]]:
    """Extract the release records from a supported manifest document."""
    document = _manifest_document(loaded)
    return _entry_mappings(_document_entries(document))


def _manifest_document(loaded: object) -> dict[str, object]:
    """Require the documented manifest schema before reading releases."""
    if not isinstance(loaded, dict):
        _raise_unsupported_schema()
    document = typ.cast("dict[str, object]", loaded)
    if document.get("schema_version") != 1:
        _raise_unsupported_schema()
    return document


def _raise_unsupported_schema() -> None:
    """Raise the manifest-schema error used by the action contract."""
    message = "CLI manifest has an unsupported schema"
    raise InstallError(message)


def _document_entries(document: dict[str, object]) -> list[object]:
    """Return the non-empty release array from a manifest document."""
    entries = document.get("releases")
    if not isinstance(entries, list):
        _raise_missing_releases()
    if not entries:
        _raise_missing_releases()
    return typ.cast("list[object]", entries)


def _raise_missing_releases() -> None:
    """Raise the missing-release-list error used by the action contract."""
    message = "CLI manifest has no releases"
    raise InstallError(message)


def _entry_mappings(entries: list[object]) -> list[dict[str, object]]:
    """Require every manifest release entry to be a mapping."""
    release_entries: list[dict[str, object]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            message = "CLI manifest contains a non-object release"
            raise InstallError(message)
        release_entries.append(entry)
    return release_entries


def _manifest_members(members: object) -> tuple[str, ...]:
    """Return valid, distinct single-file member names from the manifest."""
    member_list = _member_list(members)
    names = tuple(
        member for member in member_list if isinstance(member, str) and member
    )
    _validate_member_names(member_list, names)
    return names


def _member_list(members: object) -> list[object]:
    """Require the manifest archive member list."""
    if not isinstance(members, list):
        message = "manifest release has invalid platform or members"
        raise InstallError(message)
    return members


def _validate_member_names(member_list: list[object], names: tuple[str, ...]) -> None:
    """Require non-empty, unique, path-safe member names."""
    if len(names) != len(member_list):
        _raise_invalid_archive_member()
    if len(set(names)) != len(names):
        _raise_invalid_archive_member()
    if any(not safe_member(name) for name in names):
        _raise_invalid_archive_member()


def _raise_invalid_archive_member() -> None:
    """Raise the malformed-manifest-member error used by the action contract."""
    message = "manifest release has an invalid archive member"
    raise InstallError(message)


def _release_from_entry(entry: dict[str, object]) -> Release:
    """Build and validate one release from a manifest record."""
    platform = entry.get("platform")
    if not isinstance(platform, dict):
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
        archive_members=_manifest_members(entry.get("archive_members")),
    )
    _validate_release(release)
    return release


def _validate_release(release: Release) -> None:
    """Confirm a release contains an approved immutable archive reference."""
    if not _BUILD.fullmatch(release.build):
        message = "manifest release has an invalid build identifier"
        raise InstallError(message)
    if not _SHA256.fullmatch(release.archive_sha256):
        message = "manifest release has a missing or invalid archive digest"
        raise InstallError(message)
    validate_archive_url(release.archive_url)
    if release.member not in release.archive_members:
        message = "manifest release has no expected archive member"
        raise InstallError(message)


def validate_archive_url(url: str) -> None:
    """Require an official direct HTTPS archive URL."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != _DOWNLOAD_HOST:
        message = "manifest archive URL is not an approved HTTPS download"
        raise InstallError(message)


def load_manifest(path: Path) -> tuple[Release, ...]:
    """Load and validate the committed archive trust anchor."""
    return tuple(
        _release_from_entry(entry) for entry in _release_entries(_read_manifest(path))
    )


def resolve(manifest: Path, request: ResolutionRequest) -> Release:
    """Select exactly one approved release and reject caller disagreement."""
    requested = _requested_version(request.version)
    release = _matching_release(load_manifest(manifest), requested, request.platform)
    _validate_caller_checksum(request.caller_checksum, release)
    return release


def _requested_version(version: str) -> str:
    """Return an explicit requested version or reject an empty value."""
    requested = version.strip()
    if not requested:
        message = "cli-version must name a manifest version"
        raise InstallError(message)
    return requested


def _matching_release(
    releases: tuple[Release, ...], version: str, platform: RunnerPlatform
) -> Release:
    """Find the sole release for a version and runner platform."""
    matches = [
        release for release in releases if _matches_platform(release, version, platform)
    ]
    if not matches:
        message = (
            f"no approved cs-coverage archive for version {version!r} on "
            f"{platform.os_name}/{platform.arch}"
        )
        raise InstallError(message)
    if len(matches) != 1:
        message = "CLI manifest resolves ambiguously"
        raise InstallError(message)
    return matches[0]


def _matches_platform(release: Release, version: str, platform: RunnerPlatform) -> bool:
    """Return whether a release is for the requested runner."""
    return (
        release.version == version
        and release.os_name == platform.os_name.lower()
        and release.arch == platform.arch.lower()
    )


def _validate_caller_checksum(checksum: str, release: Release) -> None:
    """Reject a caller checksum that differs from the manifest digest."""
    supplied = checksum.strip().lower()
    if supplied and supplied != release.archive_sha256:
        message = "caller archive checksum conflicts with the manifest"
        raise InstallError(message)


def safe_member(name: str) -> bool:
    """Return whether a zip member is a single safe file name."""
    path = Path(name)
    return (
        "\\" not in name
        and name not in {".", ".."}
        and not path.is_absolute()
        and ".." not in path.parts
        and len(path.parts) == 1
    )
