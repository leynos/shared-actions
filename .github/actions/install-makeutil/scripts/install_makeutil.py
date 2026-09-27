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
import os
import re
import sys
import typing as typ
from pathlib import Path

from makeutil_errors import (
    InvalidInputError,
    UnknownVersionError,
    UnsupportedPlatformError,
)
from makeutil_verify import CACHED, INSTALLED, AssetUrls, install_makeutil

if typ.TYPE_CHECKING:  # pragma: no cover - typing only
    import collections.abc as cabc

#: Repository the prebuilt release assets are published from.
REPOSITORY = "leynos/makeutil"

#: Name the installed executable is given inside ``bin-dir``.
BINARY_NAME = "makeutil"

_MAX_BIN_DIR_LENGTH = 240

_VERSION_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")

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


def _reject_crlf_in_bin_dir(bin_dir_input: str) -> None:
    """Reject a `bin-dir` containing a carriage return or newline."""
    if "\r" in bin_dir_input or "\n" in bin_dir_input:
        msg = "bin-dir must not contain a carriage return or newline"
        raise InvalidInputError(msg)


def _reject_overlong_bin_dir(bin_dir_input: str) -> None:
    """Reject a `bin-dir` longer than the runner-safe ceiling."""
    if len(bin_dir_input) > _MAX_BIN_DIR_LENGTH:
        msg = f"bin-dir must be at most {_MAX_BIN_DIR_LENGTH} characters"
        raise InvalidInputError(msg)


def _expand_bin_dir(bin_dir_input: str) -> str:
    """Expand an absolute or `~/`-relative `bin-dir` to a plain path string."""
    if bin_dir_input == "~" or bin_dir_input.startswith("~/"):
        return str(Path.home()) + bin_dir_input[1:]
    if bin_dir_input.startswith("/"):
        return bin_dir_input
    msg = "bin-dir must be an absolute path or start with ~/"
    raise InvalidInputError(msg)


def _reject_parent_components(expanded_bin_dir: str) -> None:
    """Reject an expanded `bin-dir` containing a parent-directory component."""
    if "/../" in f"/{expanded_bin_dir}/":
        msg = "bin-dir must not contain parent-directory components"
        raise InvalidInputError(msg)


def _reject_path_separator(expanded_bin_dir: str) -> None:
    """Reject an expanded `bin-dir` containing the runner PATH separator."""
    if ":" in expanded_bin_dir:
        msg = "bin-dir must not contain the runner PATH separator"
        raise InvalidInputError(msg)


def _create_bin_dir(bin_dir_input: str, expanded_bin_dir: str) -> Path:
    """Create the expanded `bin-dir` and return its resolved, absolute form.

    `bin_dir_input` is threaded through separately so a creation failure's
    message names the value the caller supplied, not the expanded form.
    """
    path = Path(expanded_bin_dir)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        msg = f"bin-dir {bin_dir_input} could not be created on this runner"
        raise InvalidInputError(msg) from error
    return path.resolve()


def resolve_bin_dir(bin_dir_input: str) -> Path:
    """Validate `bin_dir_input` and return it as an absolute, existing path.

    Parameters
    ----------
    bin_dir_input : str
        The raw `bin-dir` input: an absolute path, or one starting `~/`.

    Returns
    -------
    Path
        The resolved, created directory.

    Raises
    ------
    InvalidInputError
        If the input is malformed, or the directory could not be created.
    """
    _reject_crlf_in_bin_dir(bin_dir_input)
    _reject_overlong_bin_dir(bin_dir_input)
    expanded_bin_dir = _expand_bin_dir(bin_dir_input)
    _reject_parent_components(expanded_bin_dir)
    _reject_path_separator(expanded_bin_dir)
    return _create_bin_dir(bin_dir_input, expanded_bin_dir)


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


def _emit_metric(env: cabc.Mapping[str, str], result: str) -> None:
    """Record `install-makeutil.result=<result>` to the log and the summary."""
    line = f"install-makeutil.result={result}"
    print(f"::notice title=Install makeutil::{line}")
    summary_path = env.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as handle:
            handle.write(f"{line}\n")


def _emit_error(title: str, message: str) -> None:
    """Print a `::error` annotation, which GitHub Actions renders on the job."""
    print(f"::error title={title}::{message}", file=sys.stderr)


def _run_resolve(args: argparse.Namespace, env: cabc.Mapping[str, str]) -> int:
    """Resolve the install plan and publish it as step outputs.

    This is the pure half of the action: nothing is downloaded or installed
    here, only decided. A refusal at this stage - a malformed input, an
    unsupported platform, or an unpinned version - reports its own bounded
    metric and never reaches the download step.
    """
    try:
        validate_version(args.version)
        bin_dir = resolve_bin_dir(args.bin_dir)
        target = resolve_target(args.runner_os, args.runner_arch)
        table_digest = lookup_digest(args.version, target)
    except InvalidInputError as error:
        _emit_error("Invalid install-makeutil input", str(error))
        _emit_metric(env, "invalid-input")
        return 1
    except UnsupportedPlatformError as error:
        _emit_error("Install makeutil failed", str(error))
        _emit_metric(env, "unsupported-platform")
        return 1
    except UnknownVersionError as error:
        _emit_error("Install makeutil failed", str(error))
        _emit_metric(env, "unknown-version")
        return 1

    expected_sha256 = args.sha256_override or table_digest
    binary_url, sidecar_url = build_asset_urls(args.version, target)
    executable_path = bin_dir / BINARY_NAME
    cache_key = build_cache_key(args.version, target, table_digest)

    outputs = (
        ("target", target),
        ("bin-dir", str(bin_dir)),
        ("executable-path", str(executable_path)),
        ("expected-sha256", expected_sha256),
        ("binary-url", binary_url),
        ("sidecar-url", sidecar_url),
        ("cache-key", cache_key),
        ("version", args.version),
    )
    for name, value in outputs:
        _append_output(env, name, value)
    return 0


def _run_install(args: argparse.Namespace, env: cabc.Mapping[str, str]) -> int:
    """Install (or reuse) the verified binary and publish the outputs."""
    result = install_makeutil(
        executable_path=Path(args.executable_path),
        expected_sha256=args.expected_sha256,
        asset_urls=AssetUrls(binary=args.binary_url, sidecar=args.sidecar_url),
    )
    _emit_metric(env, result.outcome)
    if result.outcome not in {CACHED, INSTALLED}:
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
