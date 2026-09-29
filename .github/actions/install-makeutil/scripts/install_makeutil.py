#!/usr/bin/env python3
"""Install makeutil's prebuilt static Linux binary, verified twice.

This script never builds makeutil from source and never calls ``cargo`` or
``cargo-binstall``. It is the command layer: `makeutil_plan` decides the plan
(target triple, pinned SHA-256 digest, URLs, cache key), `makeutil_verify`
holds the download and verification policy, and `makeutil_store` is the only
module that touches the installed binary on disk. This script turns those
results into step outputs, metrics and annotations.

The composite action calls this script twice: once with the ``resolve``
subcommand, which is pure and publishes the install plan as step outputs, and
once with the ``install`` subcommand, which performs the download,
verification and staged installation. Splitting the two lets the action
insert an ``actions/cache`` step keyed on the plan between them.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
import typing as typ
from pathlib import Path

from makeutil_errors import (
    InvalidInputError,
    StoreError,
    UnknownVersionError,
    UnsupportedPlatformError,
)
from makeutil_plan import PlanRequest, resolve_plan
from makeutil_store import FilesystemBinaryStore
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

    from makeutil_store import BinaryStore


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
            PlanRequest(
                version=args.version,
                bin_dir_input=args.bin_dir,
                sha256_override=args.sha256_override,
                runner_os=args.runner_os,
                runner_arch=args.runner_arch,
                home=_ambient_home(),
            )
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


def _discard_rejected_restore(store: BinaryStore, cache_hit: str) -> None:
    """Remove a restored binary the install rejected, so it cannot stay usable.

    Only a file the cache step restored (`cache-hit` is `true`) is removed; one
    that was already in `bin-dir` is left as found. A removal that fails is
    ignored: the run is already failing and the annotation names the cause.
    """
    if cache_hit != "true":
        return
    with contextlib.suppress(StoreError):
        store.discard()


def _run_install(args: argparse.Namespace, env: cabc.Mapping[str, str]) -> int:
    """Install (or reuse) the verified binary and publish the outputs."""
    store = FilesystemBinaryStore(Path(args.executable_path))
    result = install_makeutil(
        store=store,
        expected_sha256=args.expected_sha256,
        asset_urls=AssetUrls(binary=args.binary_url, sidecar=args.sidecar_url),
    )
    _emit_result(env, result.outcome)
    _emit_metric(env, "cache", cache_state(args.cache_hit, result.outcome))
    if result.outcome not in {CACHED, INSTALLED}:
        _discard_rejected_restore(store, args.cache_hit)
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
    run = _run_resolve if args.command == "resolve" else _run_install
    try:
        return run(args, env)
    except OSError as error:
        # The step-output and summary files are the action's only channel to
        # the workflow; a write failure there is reported, not raised.
        _emit_error("Install makeutil failed", f"could not publish results: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:], os.environ))
