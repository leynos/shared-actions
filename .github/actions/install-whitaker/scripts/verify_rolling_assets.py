#!/usr/bin/env python3
"""Refuse to start an install that would fall back to a source build.

Whitaker republishes its `rolling` release on every merge to `main`. The
publish briefly left the tag without a complete asset set, and a consumer
landed in that gap: chutoro's install began in the same second a republish
deleted the release, could not fetch
`cargo-dylint-x86_64-unknown-linux-gnu-v6.0.1.tgz`, and quietly built the
Dylint tools from source instead. A source build is slow, unpinned and
invisible, so it looks like a working run.

This check runs before the installer and fails closed when an asset the target
needs is absent, naming the asset and the URL. It retries first, because the
window is short by construction and a run that merely arrived during a
republish should wait rather than fail.

Nothing here trusts a single request: the release's asset list and the
manifest's own contents are both read, so an asset set that is present but
inconsistent with the manifest is caught as well as one that is incomplete.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import typing as typ
import urllib.error
import urllib.request

if typ.TYPE_CHECKING:  # pragma: no cover - typing only
    import collections.abc as cabc

REPOSITORY = "leynos/whitaker"
ROLLING_TAG = "rolling"
RELEASE_API = f"https://api.github.com/repos/{REPOSITORY}/releases/tags/{ROLLING_TAG}"
DOWNLOAD_BASE = f"https://github.com/{REPOSITORY}/releases/download/{ROLLING_TAG}"

#: Tool archives the installer fetches rather than builds. These are the
#: assets whose absence produced a silent `cargo install`.
DYLINT_TOOL_PREFIXES = ("cargo-dylint-", "dylint-link-")


class AssetsUnavailableError(RuntimeError):
    """Raised when the rolling release cannot satisfy this target."""


class Attempt(typ.NamedTuple):
    """One bounded retry schedule."""

    count: int
    interval: float


def _read(url: str, token: str | None) -> bytes:
    """Fetch one URL, sending a token when the caller supplied one."""
    request = urllib.request.Request(url)  # noqa: S310 - fixed https origins
    request.add_header("Accept", "application/vnd.github+json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return typ.cast("bytes", response.read())


def _asset_names(token: str | None) -> set[str]:
    """Return every asset name published on the rolling release."""
    payload = json.loads(_read(RELEASE_API, token))
    return {asset["name"] for asset in payload.get("assets", [])}


def _manifest(target: str, token: str | None) -> dict[str, typ.Any]:
    """Return the per-target manifest the installer will read."""
    url = f"{DOWNLOAD_BASE}/manifest-{target}.json"
    return typ.cast("dict[str, typ.Any]", json.loads(_read(url, token)))


def _missing(target: str, names: set[str], manifest: dict[str, typ.Any]) -> list[str]:
    """Return the assets this target needs and the release does not have.

    The lint archive is derived from the manifest rather than guessed, so a
    manifest that survived a republish while its archive did not is reported
    as missing rather than passing on the manifest's presence alone.
    """
    missing = [
        f"{prefix}{target}-<version>.<tgz|zip>"
        for prefix in DYLINT_TOOL_PREFIXES
        if not any(name.startswith(f"{prefix}{target}-") for name in names)
    ]
    sha = manifest.get("git_sha")
    toolchain = manifest.get("toolchain")
    if not sha or not toolchain:
        message = "manifest is missing git_sha or toolchain"
        raise AssetsUnavailableError(message)
    archive = f"whitaker-lints-{sha}-{toolchain}-{target}.tar.zst"
    if archive not in names:
        missing.append(archive)
    return missing


def verify(target: str, attempts: Attempt, token: str | None) -> str:
    """Return the manifest's toolchain once every needed asset is published.

    Parameters
    ----------
    target : str
        The Rust target triple the runner needs.
    attempts : Attempt
        How many times to look, and how long to wait between looks.
    token : str | None
        A GitHub token, used only to avoid the unauthenticated rate limit.

    Returns
    -------
    str
        The nightly toolchain the published libraries were built with.

    Raises
    ------
    AssetsUnavailableError
        If the release still cannot satisfy the target after every attempt.
    """
    last: str = "no attempt was made"
    for attempt in range(1, attempts.count + 1):
        try:
            names = _asset_names(token)
            manifest = _manifest(target, token)
        except (urllib.error.URLError, json.JSONDecodeError, OSError) as error:
            last = f"could not read the rolling release: {error}"
        else:
            missing = _missing(target, names, manifest)
            if not missing:
                return str(manifest["toolchain"])
            last = "the rolling release is missing: " + ", ".join(missing)
        if attempt < attempts.count:
            # stderr, not stdout. The caller captures this script's stdout as
            # the toolchain, and the retry path is exactly the republish case
            # this loop exists for, so a diagnostic here would end up in the
            # metric every time the retry did its job.
            print(
                f"attempt {attempt}/{attempts.count}: {last}; retrying",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(attempts.interval)
    message = (
        f"{last}. A republish takes a few seconds, so this is either a longer "
        f"outage or a genuine gap in the release. Assets are published under "
        f"{DOWNLOAD_BASE}/."
    )
    raise AssetsUnavailableError(message)


def parse_args(argv: cabc.Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, help="Rust target triple")
    parser.add_argument("--attempts", type=int, default=5)
    parser.add_argument("--interval", type=float, default=6.0)
    return parser.parse_args(argv)


def main(argv: cabc.Sequence[str] | None = None) -> int:
    """Verify the rolling assets and print the toolchain, or fail closed."""
    args = parse_args(argv)
    if args.attempts < 1:
        print("::error::attempts must be at least 1", file=sys.stderr)
        return 2
    token = os.environ.get("GITHUB_TOKEN") or None
    try:
        toolchain = verify(args.target, Attempt(args.attempts, args.interval), token)
    except AssetsUnavailableError as error:
        print(f"::error title=Whitaker rolling assets::{error}", file=sys.stderr)
        return 1
    print(toolchain)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
