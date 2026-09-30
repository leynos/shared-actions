"""The install plan: pure decisions from the action's inputs.

Version and digest validation, the runner-to-target mapping, the pinned digest
table, the release URLs and the cache key all live here, and none of it touches
the environment, the filesystem or the network. `resolve_plan` is the query the
`resolve` subcommand publishes; the CLI in `install_makeutil.py` is the only
place that turns a plan into step outputs.
"""

from __future__ import annotations

import dataclasses as dc
import re
import typing as typ

from makeutil_errors import (
    InvalidInputError,
    UnknownVersionError,
    UnsupportedPlatformError,
)

if typ.TYPE_CHECKING:  # pragma: no cover - typing only
    from pathlib import Path

#: Repository the prebuilt release assets are published from.
REPOSITORY = "leynos/makeutil"

#: Name the installed executable is given inside ``bin-dir``.
BINARY_NAME = "makeutil"

_VERSION_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")

#: A pinned digest, and any valid override, is always 64 lowercase hex
#: characters; anything else - including a newline that would smuggle an
#: extra `GITHUB_OUTPUT` record - is refused before any output is written.
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

#: Rust target triple keyed by (``runner.os``, ``runner.arch``). Linux only:
#: makeutil publishes no other platform's static binary, and this action
#: never falls back to a source build for one it does not.
_TARGETS: dict[tuple[str, str], str] = {
    ("Linux", "X64"): "x86_64-unknown-linux-musl",
    ("Linux", "ARM64"): "aarch64-unknown-linux-musl",
}

#: SHA-256 of the published binary asset, keyed by (version, target). A
#: version missing from this table is refused rather than installed
#: unverified; add its digests here before pinning a new version.
_DIGESTS: dict[tuple[str, str], str] = {
    ("0.1.0", "x86_64-unknown-linux-musl"): (
        "99dd28a138dbe07e88e4dc5dd3954e6b29b46cc959635311d326cb537253115d"
    ),
    ("0.1.0", "aarch64-unknown-linux-musl"): (
        "72b1513eb8640ee18ec0298cb9c0209985a339826863e24e186ceb0929a1c05b"
    ),
    ("0.1.1", "x86_64-unknown-linux-musl"): (
        "be86dd00994ffeb81fedaa660cf007366a5de87bccab5c73ec1153b2ad7fe26c"
    ),
    ("0.1.1", "aarch64-unknown-linux-musl"): (
        "8ec3eebd0e2af7cf0e087dc810defc7c065c3938a5179cdb0b3e25148659aaa4"
    ),
    ("0.1.2", "x86_64-unknown-linux-musl"): (
        "688c3385ac2f5cb630e8a0ffe640815f366793195d35e89821b918b09531adf8"
    ),
    ("0.1.2", "aarch64-unknown-linux-musl"): (
        "ad0d00c25739c9aaee2e1f3aa02749580c580e422cafb03ec3507f4f2ab28e70"
    ),
}


def validate_version(version: str) -> None:
    """Validate that `version` is three numeric components, no leading zeros."""
    if not _VERSION_RE.match(version):
        msg = "version must be three numeric components without leading zeros"
        raise InvalidInputError(msg)


def validate_sha256_override(sha256_override: str) -> None:
    """Validate a non-empty `sha256-override` is 64 lowercase hex characters.

    An empty override is not validated here; it means "use the pinned table
    digest" and is handled by the caller.

    Raises
    ------
    InvalidInputError
        If the override is non-empty and not exactly 64 lowercase hex
        characters.
    """
    if sha256_override and not _SHA256_RE.match(sha256_override):
        msg = "sha256-override must be 64 lowercase hexadecimal characters"
        raise InvalidInputError(msg)


def resolve_target(runner_os: str, runner_arch: str) -> str:
    """Map a runner's OS/architecture pair to makeutil's target triple.

    Raises
    ------
    UnsupportedPlatformError
        If makeutil publishes no prebuilt binary for the pair.
    """
    try:
        return _TARGETS[(runner_os, runner_arch)]
    except KeyError:
        supported = ", ".join(f"{os_}/{arch}" for os_, arch in _TARGETS)
        msg = (
            f"makeutil publishes no prebuilt binary for "
            f"{runner_os}/{runner_arch}; supported platforms are {supported}"
        )
        raise UnsupportedPlatformError(msg) from None


def lookup_digest(version: str, target: str) -> str:
    """Return the pinned SHA-256 digest for `version` on `target`.

    Raises
    ------
    UnknownVersionError
        If the table has no entry for the pair. There is no floating or
        latest version, so an unpinned one is refused rather than installed
        unverified; the table must gain the version's digests first.
    """
    try:
        return _DIGESTS[(version, target)]
    except KeyError:
        msg = (
            f"no pinned digest for makeutil {version} on {target}; the "
            f"digest table in makeutil_plan.py must gain this version's "
            f"digests before it can be installed"
        )
        raise UnknownVersionError(msg) from None


def build_asset_urls(version: str, target: str) -> tuple[str, str]:
    """Return the (binary, sidecar) download URLs for `version` on `target`."""
    base = (
        f"https://github.com/{REPOSITORY}/releases/download/"
        f"v{version}/makeutil-{target}"
    )
    return base, f"{base}.sha256"


def build_cache_key(version: str, target: str, table_digest: str) -> str:
    """Return the cache key this action uses for `bin-dir`'s makeutil binary."""
    return f"install-makeutil-{version}-{target}-{table_digest}"


@dc.dataclass(slots=True, frozen=True)
class InstallPlan:
    """Everything the `install` step and the cache step need, decided up front."""

    target: str
    bin_dir: Path
    executable_path: Path
    expected_sha256: str
    binary_url: str
    sidecar_url: str
    cache_key: str
    version: str

    def outputs(self) -> tuple[tuple[str, str], ...]:
        """Return the plan as the `(name, value)` step outputs it publishes."""
        return (
            ("target", self.target),
            ("bin-dir", str(self.bin_dir)),
            ("executable-path", str(self.executable_path)),
            ("expected-sha256", self.expected_sha256),
            ("binary-url", self.binary_url),
            ("sidecar-url", self.sidecar_url),
            ("cache-key", self.cache_key),
            ("version", self.version),
        )


@dc.dataclass(slots=True, frozen=True)
class PlanRequest:
    """The action's inputs and the runner facts `resolve_plan` decides from."""

    version: str
    bin_dir: Path
    sha256_override: str
    runner_os: str
    runner_arch: str


def resolve_plan(request: PlanRequest) -> InstallPlan:
    """Decide the install plan from the action's inputs, touching nothing.

    This is the query half of `resolve`: value-based validation, the target
    lookup, URL construction and the cache key, and nothing else. It touches no
    filesystem and reads no environment: the request carries an already
    resolved `bin_dir`, which the command layer obtains from `makeutil_bin_dir`.

    Raises
    ------
    InvalidInputError
        If the version or the digest override is malformed.
    UnsupportedPlatformError
        If makeutil publishes no prebuilt binary for the runner.
    UnknownVersionError
        If the digest table has no entry for the version and target.
    """
    validate_version(request.version)
    validate_sha256_override(request.sha256_override)
    bin_dir = request.bin_dir
    target = resolve_target(request.runner_os, request.runner_arch)
    table_digest = lookup_digest(request.version, target)
    binary_url, sidecar_url = build_asset_urls(request.version, target)
    return InstallPlan(
        target=target,
        bin_dir=bin_dir,
        executable_path=bin_dir / BINARY_NAME,
        expected_sha256=request.sha256_override or table_digest,
        binary_url=binary_url,
        sidecar_url=sidecar_url,
        cache_key=build_cache_key(request.version, target, table_digest),
        version=request.version,
    )
