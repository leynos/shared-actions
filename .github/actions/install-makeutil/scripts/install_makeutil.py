#!/usr/bin/env python3
"""Install makeutil's prebuilt static Linux binary, verified twice.

This script never builds makeutil from source and never calls ``cargo`` or
``cargo-binstall``. It resolves the runner's target triple, looks up the
pinned SHA-256 digest for the requested version in the table below, and
delegates the download, verification and staged installation to
`makeutil_verify`.

The composite action calls this script twice: once with the ``resolve``
subcommand, which is pure and publishes the install plan as step outputs, and
once with the ``install`` subcommand, which performs the download,
verification and staged installation. Splitting the two lets the action
insert an ``actions/cache`` step keyed on the plan between them.
"""

from __future__ import annotations

import argparse
import dataclasses as dc
import os
import re
import sys
import typing as typ
from pathlib import Path

from makeutil_bin_dir import resolve_bin_dir
from makeutil_errors import (
    InvalidInputError,
    UnknownVersionError,
    UnsupportedPlatformError,
)
from makeutil_verify import (
    CACHE_HIT,
    CACHE_MISS,
    CACHE_STALE,
    CACHED,
    INSTALLED,
    AssetUrls,
    install_makeutil,
)

if typ.TYPE_CHECKING:  # pragma: no cover - typing only
    import collections.abc as cabc

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
            f"digest table in install_makeutil.py must gain this version's "
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


def _append_output(env: cabc.Mapping[str, str], name: str, value: str) -> None:
    """Append one `name=value` line to the file `GITHUB_OUTPUT` names."""
    output_path = env.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with Path(output_path).open("a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def _emit_metric(env: cabc.Mapping[str, str], name: str, value: str) -> None:
    """Record `install-makeutil.<name>=<value>` to the log and the summary."""
    line = f"install-makeutil.{name}={value}"
    print(f"::notice title=Install makeutil::{line}")
    summary_path = env.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as handle:
            handle.write(f"{line}\n")


def _emit_result(env: cabc.Mapping[str, str], result: str) -> None:
    """Record the run's terminal `install-makeutil.result`."""
    _emit_metric(env, "result", result)


def cache_state(cache_hit: str, outcome: str) -> str:
    """Classify the run's cache use from the cache step's `cache-hit` output.

    The result alone cannot tell a plain miss from a restored entry that was
    rejected and replaced, so the cache step's own signal is folded in:
    `cache-hit` is `true` only when the exact key was restored.
    """
    if outcome == CACHED:
        return CACHE_HIT
    return CACHE_STALE if cache_hit == "true" else CACHE_MISS


def _emit_error(title: str, message: str) -> None:
    """Print a `::error` annotation, which GitHub Actions renders on the job."""
    print(f"::error title={title}::{message}", file=sys.stderr)


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


def resolve_plan(
    *,
    version: str,
    bin_dir_input: str,
    sha256_override: str,
    runner_os: str,
    runner_arch: str,
    home: Path | None,
) -> InstallPlan:
    """Decide the install plan from the action's inputs, touching nothing.

    This is the query half of `resolve`: it reads no environment, writes no
    output and creates no directory. The home directory is passed in.

    Raises
    ------
    InvalidInputError
        If an input is malformed or `bin-dir` cannot be resolved.
    UnsupportedPlatformError
        If makeutil publishes no prebuilt binary for the runner.
    UnknownVersionError
        If the digest table has no entry for the version and target.
    """
    validate_version(version)
    validate_sha256_override(sha256_override)
    bin_dir = resolve_bin_dir(bin_dir_input, home)
    target = resolve_target(runner_os, runner_arch)
    table_digest = lookup_digest(version, target)
    binary_url, sidecar_url = build_asset_urls(version, target)
    return InstallPlan(
        target=target,
        bin_dir=bin_dir,
        executable_path=bin_dir / BINARY_NAME,
        expected_sha256=sha256_override or table_digest,
        binary_url=binary_url,
        sidecar_url=sidecar_url,
        cache_key=build_cache_key(version, target, table_digest),
        version=version,
    )


def _ambient_home() -> Path | None:
    """Return the runner's home directory, or `None` where it has none."""
    try:
        return Path.home()
    except (RuntimeError, KeyError):
        return None


def _run_resolve(args: argparse.Namespace, env: cabc.Mapping[str, str]) -> int:
    """Publish the install plan as step outputs, or report why it was refused.

    This is the command half of `resolve`, the only place that writes outputs,
    the summary or annotations. A refusal - a malformed input, an unsupported
    platform, or an unpinned version - reports its own bounded metric and never
    reaches the download step.
    """
    try:
        plan = resolve_plan(
            version=args.version,
            bin_dir_input=args.bin_dir,
            sha256_override=args.sha256_override,
            runner_os=args.runner_os,
            runner_arch=args.runner_arch,
            home=_ambient_home(),
        )
    except InvalidInputError as error:
        _emit_error("Invalid install-makeutil input", str(error))
        _emit_result(env, "invalid-input")
        return 1
    except UnsupportedPlatformError as error:
        _emit_error("Install makeutil failed", str(error))
        _emit_result(env, "unsupported-platform")
        return 1
    except UnknownVersionError as error:
        _emit_error("Install makeutil failed", str(error))
        _emit_result(env, "unknown-version")
        return 1

    for name, value in plan.outputs():
        _append_output(env, name, value)
    return 0


def _discard_rejected_restore(executable_path: Path, cache_hit: str) -> None:
    """Remove a restored binary the install rejected, so it cannot stay usable.

    Only a file the cache step restored (`cache-hit` is `true`) is removed; one
    that was already in `bin-dir` is left as found. A removal that fails is
    ignored: the run is already failing and the annotation names the cause.
    """
    if cache_hit != "true":
        return
    try:
        executable_path.unlink(missing_ok=True)
    except OSError:
        return


def _run_install(args: argparse.Namespace, env: cabc.Mapping[str, str]) -> int:
    """Install (or reuse) the verified binary and publish the outputs."""
    result = install_makeutil(
        executable_path=Path(args.executable_path),
        expected_sha256=args.expected_sha256,
        asset_urls=AssetUrls(binary=args.binary_url, sidecar=args.sidecar_url),
    )
    _emit_result(env, result.outcome)
    _emit_metric(env, "cache", cache_state(args.cache_hit, result.outcome))
    if result.outcome not in {CACHED, INSTALLED}:
        _discard_rejected_restore(Path(args.executable_path), args.cache_hit)
        _emit_error("Install makeutil failed", result.message)
        return 1
    _append_output(env, "path", str(result.path))
    _append_output(env, "version", args.version)
    _append_output(env, "result", result.outcome)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    """Build the two-subcommand parser this script's CLI dispatches on."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    resolve = subparsers.add_parser(
        "resolve", help="Resolve the install plan and publish step outputs"
    )
    resolve.add_argument("--version", required=True)
    resolve.add_argument("--bin-dir", required=True)
    resolve.add_argument("--sha256-override", default="")
    resolve.add_argument("--runner-os", required=True)
    resolve.add_argument("--runner-arch", required=True)

    install = subparsers.add_parser(
        "install", help="Download, verify and install the resolved binary"
    )
    install.add_argument("--executable-path", required=True)
    install.add_argument("--expected-sha256", required=True)
    install.add_argument("--binary-url", required=True)
    install.add_argument("--sidecar-url", required=True)
    install.add_argument("--version", required=True)
    install.add_argument("--cache-hit", default="")

    return parser


def main(argv: cabc.Sequence[str], env: cabc.Mapping[str, str]) -> int:
    """Dispatch to the `resolve` or `install` subcommand.

    Parameters
    ----------
    argv : collections.abc.Sequence of str
        The command line, excluding the program name.
    env : collections.abc.Mapping of str to str
        The environment to read `GITHUB_OUTPUT`/`GITHUB_STEP_SUMMARY` from
        and to publish outputs into. Passed explicitly, rather than read from
        `os.environ`, so a test can supply one without mutating the process
        environment.

    Returns
    -------
    int
        The process exit code.
    """
    args = _build_parser().parse_args(argv)
    if args.command == "resolve":
        return _run_resolve(args, env)
    return _run_install(args, env)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:], os.environ))
