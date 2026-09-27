#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Assert what setup-rust's mold installer did on a real runner.

``test-setup-rust-mold.yml`` calls this after each step it checks, so every
decision the workflow makes lives here rather than in shell:

- ``outcome`` compares the action's outputs with what the job expects and,
  for an install or a reuse, that ``mold`` and ``ld.mold`` resolve on
  ``PATH`` and report the version;
- ``linked-by-mold`` reads a binary's ``.comment`` section, where mold
  records itself, to prove a build really linked with it;
- ``tampered`` serves a one-byte-altered copy of the pinned archive and
  asserts the installer refuses it and leaves no tree behind.

Exit status is 0 when the assertion holds and 1 with a GitHub ``::error``
annotation when it does not.
"""

from __future__ import annotations

import argparse
import importlib.util
import shutil
import subprocess
import sys
import typing as typ
from pathlib import Path

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from types import ModuleType

INSTALLER = (
    Path(__file__).resolve().parents[1] / "actions/setup-rust/scripts/install_mold.py"
)


class MoldAssertionError(Exception):
    """An expectation the runner did not meet."""


def _load_installer() -> ModuleType:
    """Import setup-rust's installer by path, to reuse its pins and entry point."""
    spec = importlib.util.spec_from_file_location("install_mold", INSTALLER)
    if spec is None or spec.loader is None:
        msg = f"cannot load {INSTALLER}"
        raise MoldAssertionError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _require(condition: object, message: str) -> None:
    """Raise :class:`MoldAssertionError` with *message* unless *condition* holds."""
    if not condition:
        raise MoldAssertionError(message)


def _run(*command: str) -> str:
    """Return a command's standard output, failing when it cannot run."""
    result = subprocess.run(  # noqa: S603, TID251 - stdlib only; fixed commands.
        command, check=False, capture_output=True, text=True
    )
    _require(result.returncode == 0, f"{command[0]} failed: {result.stderr.strip()}")
    return result.stdout


def check_outcome(args: argparse.Namespace) -> None:
    """Hold the action's outputs, and PATH, to what the job expects."""
    _require(
        args.status == args.expect_status,
        f"mold-status is {args.status!r}, expected {args.expect_status!r}",
    )
    _require(
        args.version == args.expect_version,
        f"mold-version is {args.version!r}, expected {args.expect_version!r}",
    )
    if args.expect_status == "skipped":
        return
    for name in ("mold", "ld.mold"):
        found = shutil.which(name)
        _require(found is not None, f"{name} is not on PATH")
        reported = _run(str(found), "--version").split()
        _require(
            reported[:2] == ["mold", args.expect_version],
            f"{name} reports {' '.join(reported[:2])!r}",
        )


def check_linked_by_mold(args: argparse.Namespace) -> None:
    """Require *binary*'s ``.comment`` section to name mold as its linker."""
    comment = _run("readelf", "--string-dump=.comment", str(args.binary))
    _require("mold" in comment, f"{args.binary} was not linked by mold:\n{comment}")


def check_tampered(args: argparse.Namespace) -> None:
    """Serve an altered pinned archive and require the installer to refuse it."""
    installer = _load_installer()
    release = installer.select_release(
        args.mold_version, args.runner_arch, installer.MOLD_DIGESTS
    )
    releases = args.scratch / "releases"
    archive = releases / f"v{release.version}" / release.archive_name
    archive.parent.mkdir(parents=True, exist_ok=True)
    installer.download(release.url(installer.RELEASE_BASE_URL), archive)
    with archive.open("ab") as sink:
        sink.write(b"\0")
    tool_cache = args.scratch / "tool-cache"
    status = installer.main(
        [
            "--mold-version",
            release.version,
            "--runner-arch",
            args.runner_arch,
            "--tool-cache",
            str(tool_cache),
            "--temp-dir",
            str(args.scratch / "temp"),
            "--release-base-url",
            releases.as_uri(),
        ]
    )
    _require(status == 1, f"the tampered archive was accepted (exit {status})")
    left = [path for path in tool_cache.rglob("*") if not path.is_dir()]
    _require(not left, f"a refused archive left files in the tool cache: {left}")


def _parser() -> argparse.ArgumentParser:
    """Return the command-line parser with one subcommand per assertion."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    outcome = commands.add_parser("outcome")
    outcome.add_argument("--status", required=True)
    outcome.add_argument("--version", required=True)
    outcome.add_argument("--expect-status", required=True)
    outcome.add_argument("--expect-version", required=True)
    outcome.set_defaults(check=check_outcome)

    linked = commands.add_parser("linked-by-mold")
    linked.add_argument("--binary", required=True, type=Path)
    linked.set_defaults(check=check_linked_by_mold)

    tampered = commands.add_parser("tampered")
    tampered.add_argument("--mold-version", required=True)
    tampered.add_argument("--runner-arch", required=True)
    tampered.add_argument("--scratch", required=True, type=Path)
    tampered.set_defaults(check=check_tampered)
    return parser


def main(argv: cabc.Sequence[str] | None = None) -> int:
    """Run the named assertion and report a failure as a GitHub error."""
    args = _parser().parse_args(argv)
    try:
        args.check(args)
    except MoldAssertionError as failure:
        print(f"::error title=mold assertion::{failure}", file=sys.stderr)
        return 1
    print(f"{args.command}: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
